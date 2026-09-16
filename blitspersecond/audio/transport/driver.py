"""The device driver: one thread, one stream, and the engine paced by audio.

This is the only thread in the engine, and the reason for it is the whole
design. PortAudio's blocking `write` returns when the device has room for
more, so the loop

    advance one block -> write it -> repeat

is paced by the hardware itself. No timer, no sleep, no clock arithmetic:
the sound card decides how fast audio time moves, and backpressure does the
rest. Synthesis happens on this thread rather than inside a PortAudio
callback, so a slow block delays audio instead of corrupting it -- and stays
off the sixteen milliseconds the simulation and the picture share.

**Startup is a readiness handshake.** `start()` returns only once the device
is open, the stream is running, and it has accepted the first block. Device
failures come back to the caller with a traceback rather than disappearing
into a background thread and leaving silence to explain itself.

**There is no pause or resume**, deliberately. Once started the stream runs
until `close()`, through suspension, backgrounding and everything else: a
game being paused is not a reason to tear down the audio device, and
reopening one is where the clicks live.
"""

from threading import Condition, Event, Thread
from time import perf_counter, sleep
from typing import cast

from numpy import clip, empty, float32

from ..constants import BLOCK_SIZE, SAMPLE_RATE
from .devices import default_output_device
from ..synth.engine import AudioEngine

_NEW = "new"
_STARTING = "starting"
_RUNNING = "running"
_CLOSING = "closing"
_CLOSED = "closed"
_FAILED = "failed"


def _load_sounddevice():
    """Imported on use, not at module import.

    Keeps `import blitspersecond` from touching PortAudio, so an offline
    render on a machine with no sound card never goes near the library.
    It is also the seam the tests replace.
    """
    import sounddevice

    return sounddevice


class Driver:
    """Let one long-lived output stream drive one AudioEngine."""

    def __init__(self, engine: AudioEngine, latency="low"):
        if not isinstance(engine, AudioEngine):
            raise TypeError(f"expected AudioEngine, got {type(engine)}")
        self._engine = engine
        self._latency_request = latency
        self._condition = Condition()
        self._closing = Event()
        self._thread: Thread | None = None
        # Published by the worker once PortAudio has opened it, so close()
        # can abort a write that the endpoint has stopped consuming.
        self._stream = None
        self._state = _NEW
        self._error: BaseException | None = None

        self._latency: float | None = None
        self._device: str | None = None
        self._stream_blocksize: int | None = None
        self._stream_samplerate: float | None = None
        self._epoch: float | None = None
        #: One reusable stereo buffer. Clipping into it is the only copy the
        #: output path makes, and it is made once rather than per block.
        self._buffer = empty((BLOCK_SIZE, 2), dtype=float32)

        #: Underflows the host reported: audio it wanted and did not have.
        #: This is the number that means trouble.
        self.xruns = 0
        #: The longest a single blocking write took. **Not a stall gauge, and
        #: high is not bad** -- this measures how long the device kept us
        #: waiting, which is the backpressure doing its job. A healthy stream
        #: sits near one block's worth or above; a worst_write consistently
        #: near zero would mean the engine is always behind and the device is
        #: never full, which is what precedes xruns.
        self.worst_write = 0.0
        self.blocks_written = 0

    # -- lifecycle --------------------------------------------------------

    def start(self):
        """Open the permanent stream and wait for its first accepted block."""
        with self._condition:
            if self._state == _RUNNING:
                return
            if self._state == _CLOSED:
                raise RuntimeError("cannot restart a closed audio Driver")
            self._raise_if_failed()
            if self._state != _NEW:
                raise RuntimeError(f"audio Driver is already {self._state}")
            if self._engine._running:
                raise RuntimeError("AudioEngine is already driven")

            # Claimed here rather than on the worker, so a caller that gets a
            # return from start() knows the graph is frozen.
            self._engine._running = True
            self._state = _STARTING
            # Daemon, deliberately. `close()` still joins it explicitly, so
            # this changes nothing about an orderly shutdown -- it decides
            # what happens after a DISORDERLY one. A non-daemon worker keeps
            # the interpreter alive after an unhandled exception anywhere in
            # a game, turning a traceback into a hung process with no further
            # output. A stream torn down by interpreter exit is the operating
            # system's problem; a hang is the developer's.
            self._thread = Thread(
                target=self._run, name="audio-driver", daemon=True
            )
            try:
                self._thread.start()
            except BaseException:
                self._thread = None
                self._state = _NEW
                self._engine._running = False
                raise

            self._condition.wait_for(
                lambda: self._state in (_RUNNING, _FAILED, _CLOSED)
            )
            self._raise_if_failed()
            if self._state != _RUNNING:
                raise RuntimeError("audio Driver closed during startup")

    def close(self):
        """Terminally release the worker and the PortAudio stream.

        Not a gameplay state. This is teardown at process exit, beside the
        display closing.

        The active stream is published by the worker. Closing aborts it before
        joining, so a blocking write whose endpoint has stopped consuming
        cannot hold orderly process teardown.
        """
        with self._condition:
            thread = self._thread
            if thread is None:
                self._state = _CLOSED
                return
            if self._state not in (_CLOSED, _FAILED):
                self._state = _CLOSING
                self._closing.set()
                self._condition.notify_all()
            stream = self._stream
        if stream is not None:
            # PortAudio's blocking write is normally the clock. If an endpoint
            # disappears, it can instead be the thing preventing teardown.
            # abort() is the immediate, discard-pending-buffers operation and
            # is deliberately issued by the closing thread before the join.
            stream.abort()
        thread.join()

    # -- the worker -------------------------------------------------------

    def _run(self):
        stream = None
        stream_active = False
        error = None
        try:
            sounddevice = _load_sounddevice()
            selector = default_output_device(sounddevice)
            info = sounddevice.query_devices(selector, kind="output")
            host = sounddevice.query_hostapis(info["hostapi"])
            self._device = f"{host['name']}: {info['name']}"
            stream = sounddevice.OutputStream(
                samplerate=SAMPLE_RATE,
                channels=2,
                dtype="float32",
                # The host picks its own period. The engine block and the
                # device block are deliberately independent: one write may
                # carry one engine block without forcing PipeWire, ALSA,
                # WASAPI, USB or Bluetooth to adopt 400 samples.
                blocksize=0,
                latency=self._latency_request,
                device=selector,
            )
            with self._condition:
                self._stream = stream

            # A close racing device startup must not enter the first blocking
            # write after the closer already found no stream to abort.
            if self._closing.is_set():
                return

            # One block rendered BEFORE the stream starts, so the device is
            # never asked for audio that does not exist yet.
            first = self._prepare(self._engine.advance())
            stream.start()
            stream_active = True
            self._epoch = perf_counter()
            # Always a one-directional OutputStream, so `.latency` is a plain
            # float; the tuple form is for duplex streams.
            self._latency = float(cast(float, stream.latency))
            self._stream_blocksize = int(stream.blocksize)
            self._stream_samplerate = float(stream.samplerate)
            self._write(stream, first)

            with self._condition:
                self._state = _RUNNING
                self._condition.notify_all()

            while not self._closing.is_set():
                self._write(stream, self._prepare(self._engine.advance()))
        except BaseException as caught:
            # abort() commonly wakes a blocking write by making it fail. Once
            # close was requested that is the mechanism, not a device fault.
            if not self._closing.is_set():
                error = caught
        finally:
            if stream is not None:
                if stream_active:
                    try:
                        stream.stop()
                    except BaseException as caught:
                        if error is None:
                            error = caught
                try:
                    stream.close()
                except BaseException as caught:
                    if error is None:
                        error = caught

            self._engine._running = False
            with self._condition:
                self._stream = None
                self._error = error
                self._state = _FAILED if error is not None else _CLOSED
                self._condition.notify_all()

    def _prepare(self, bus):
        """Clip the settled block into the reusable device buffer.

        Rejects a mono output at the boundary. A device Stage must be a
        MixingDesk: the driver never manufactures a second channel from one,
        because "both speakers get the same thing" is a decision the graph
        should make rather than the transport.
        """
        block = bus.current.data
        if block.shape != (BLOCK_SIZE, 2):
            raise ValueError(
                f"AudioEngine returned shape {block.shape}, "
                f"expected ({BLOCK_SIZE}, 2)"
            )
        if block.dtype != float32:
            raise TypeError(
                f"AudioEngine returned dtype {block.dtype}, expected float32"
            )
        clip(block, -1.0, 1.0, out=self._buffer)
        return self._buffer

    def _write(self, stream, block):
        started = perf_counter()
        underflow = stream.write(block)
        self.worst_write = max(self.worst_write, perf_counter() - started)
        self.blocks_written += 1
        if underflow:
            self.xruns += 1

    def _raise_if_failed(self):
        if self._state == _FAILED:
            raise RuntimeError(f"audio Driver failed: {self._error}") from self._error

    # -- diagnostics -------------------------------------------------------

    @property
    def running(self) -> bool:
        with self._condition:
            return self._state == _RUNNING

    @property
    def latency(self) -> float | None:
        """What the host reported, in seconds. Not what was asked for."""
        return self._latency

    @property
    def epoch(self) -> float | None:
        """`perf_counter()` when the stream started, for correlating with the
        simulation clock from outside. The engine itself never reads it."""
        return self._epoch

    @property
    def error(self) -> BaseException | None:
        return self._error

    def stats(self) -> str:
        latency = (
            "unstarted"
            if self._latency is None
            else f"{self._latency * 1000:.1f}ms"
        )
        return (
            f"xruns={self.xruns}"
            f"  worst_write={self.worst_write * 1000:.2f}ms"
            f"  blocks_written={self.blocks_written}"
            f"  stream_blocksize={self._stream_blocksize}"
            f"  stream_latency={latency}"
            f"  device={self._device}"
        )


def play(engine: AudioEngine, seconds: float):
    """Run a standalone engine on the device for a finite wall-clock time.

    For hearing something without building a game around it. The engine's own
    clock is still advanced by the device rather than by this duration -- the
    sleep only decides when to stop asking.
    """
    if seconds < 0.0:
        raise ValueError("duration cannot be negative")
    driver = Driver(engine)
    driver.start()
    try:
        sleep(seconds)
    finally:
        driver.close()
    driver._raise_if_failed()
    return driver
