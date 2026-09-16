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

`_silence` is the one exception, and it exists because the rule is true: with
no device there is no clock, so the only way to keep audio time moving while
one is missing is to count it out. Nothing else in this file does.

**Startup is a readiness handshake.** `start()` returns only once the device
is open, the stream is running, and it has accepted the first block. Device
failures come back to the caller with a traceback rather than disappearing
into a background thread and leaving silence to explain itself.

**A device that goes away is followed, not fatal.** Batteries run out and
Bluetooth drops; neither ends the run. A write that fails while the driver is
not closing discards the dead stream, cycles PortAudio so it enumerates again,
and opens the current system default. With nothing at all to open the driver
goes `silent` -- still running, still advancing audio time -- and keeps
looking.

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

#: One engine block of wall time. Only ever used when there is no device to
#: pace against; see `Driver._silence`.
_BLOCK_SECONDS = BLOCK_SIZE / SAMPLE_RATE

#: How often a silent driver goes looking for a device again. Each attempt
#: costs a PortAudio teardown and reinitialise -- about 33ms measured -- so
#: this is deliberately far slower than a block.
_RETRY_SECONDS = 1.0

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

    def __init__(self, engine: AudioEngine, latency="low", *, logger=None):
        if not isinstance(engine, AudioEngine):
            raise TypeError(f"expected AudioEngine, got {type(engine)}")
        self._engine = engine
        self._latency_request = latency
        # Optional so the tests can drive a Driver with no logging rig, but
        # the engine always passes one: losing a device used to be entirely
        # silent, which is how a flat headset ended a run with no evidence.
        self._logger = logger
        #: Whether the stream is started, so teardown knows to stop it first.
        self._active = False
        #: True while there is no device to write to at all.
        self._silent = False
        self._silent_deadline = 0.0
        self._retry_at = 0.0
        #: Times a device went away mid-run and was followed or mourned.
        self.device_losses = 0
        #: Blocks rendered and thrown away because there was nowhere to put
        #: them. Nonzero means the player heard nothing for that long.
        self.silent_blocks = 0
        #: The last device change the loop has not published yet. Set on the
        #: worker, taken on the main thread: the audio thread must not walk a
        #: handler stack, both because a slow handler there costs xruns and
        #: because game code reached from it would be on the wrong thread.
        self._notice: tuple[str | None] | None = None
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
            try:
                stream.abort()
            except BaseException:
                # The stream read above may already be the worker's cast-off:
                # a close racing a device loss can reach a stream the worker
                # is discarding, and aborting one of those raises. The join
                # below is what teardown actually depends on, and a stream
                # nobody is writing to cannot hold it.
                pass
        thread.join()

    # -- the worker -------------------------------------------------------

    def _run(self):
        error = None
        stream = None
        try:
            sounddevice = _load_sounddevice()
            # The FIRST open is deliberately not recoverable. `start()` is
            # waiting on it, and a machine with no output device at all is a
            # failure the caller asked for and should get a traceback from.
            stream = self._open(sounddevice)
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
            self._active = True
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
                if stream is None:
                    # Nothing to write to. Audio time still has to move, and
                    # the device is retried on a far slower cadence than a
                    # block -- each retry costs a PortAudio recycle.
                    self._silence()
                    if perf_counter() >= self._retry_at:
                        stream = self._reopen(sounddevice)
                    continue
                try:
                    self._write(stream, self._prepare(self._engine.advance()))
                except BaseException as caught:
                    # Teardown re-raises; a device that went away does not.
                    if self._closing.is_set():
                        raise
                    stream = self._lost(sounddevice, stream, caught)
        except BaseException as caught:
            # abort() commonly wakes a blocking write by making it fail. Once
            # close was requested that is the mechanism, not a device fault.
            if not self._closing.is_set():
                error = caught
        finally:
            if stream is not None:
                if self._active:
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

    # -- following the device ---------------------------------------------

    def _open(self, sounddevice):
        """Open an unstarted stream on whatever the system default is NOW.

        Unstarted because the first block has to exist before the device is
        allowed to ask for one, and the caller decides when that is true.
        """
        selector = default_output_device(sounddevice)
        info = sounddevice.query_devices(selector, kind="output")
        host = sounddevice.query_hostapis(info["hostapi"])
        self._device = f"{host['name']}: {info['name']}"
        return sounddevice.OutputStream(
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

    def _lost(self, sounddevice, stream, caught):
        """A write failed and it was not teardown. Go and find a device.

        Batteries run out and Bluetooth drops. Neither is an error condition,
        and neither is a reason to end a running game's audio. Returns the
        replacement stream, or None to go quiet and keep trying.
        """
        self.device_losses += 1
        self._log(
            "warning",
            f"Audio device lost ({type(caught).__name__}: {caught}). "
            f"Following the system default.",
        )
        self._discard(stream)
        return self._reopen(sounddevice)

    def _reopen(self, sounddevice):
        """Recycle PortAudio, then open the system default if there is one.

        **The recycle is not optional.** PortAudio enumerates devices when it
        initialises and never looks again, so a process that lost its endpoint
        cannot SEE the replacement until the library is brought back up --
        measured on Windows as a returning headset staying invisible for a
        full thirty seconds until exactly this was done. Without it, "reopen
        the default" can reopen the endpoint that just died.
        """
        self._retry_at = perf_counter() + _RETRY_SECONDS
        self._recycle(sounddevice)
        try:
            stream = self._open(sounddevice)
            stream.start()
        except BaseException as caught:
            self._quieten(f"No audio device available ({caught}).")
            return None
        self._active = True
        self._latency = float(cast(float, stream.latency))
        self._stream_blocksize = int(stream.blocksize)
        self._stream_samplerate = float(stream.samplerate)
        with self._condition:
            self._stream = stream
        if self._silent:
            self._silent = False
            self._log("info", f"Audio returned on {self._device}.")
        else:
            self._log("info", f"Audio followed the default to {self._device}.")
        with self._condition:
            self._notice = (self._device,)
        return stream

    @staticmethod
    def _recycle(sounddevice):
        """Tear PortAudio down and bring it back, so it enumerates again."""
        terminate = getattr(sounddevice, "_terminate", None)
        initialize = getattr(sounddevice, "_initialize", None)
        if terminate is None or initialize is None:
            return  # a test double, or a build without the seam
        try:
            terminate()
            initialize()
        except BaseException:
            # A library that will not cycle is not a reason to stop trying to
            # play. The open that follows fails honestly if it matters.
            pass

    def _discard(self, stream):
        """Release a stream whose device is gone, expecting failure.

        Unlike terminal teardown, nothing here is worth reporting: stopping
        and closing a dead endpoint raises routinely, and says nothing the
        caller could act on.
        """
        if self._active:
            try:
                stream.stop()
            except BaseException:
                pass
            self._active = False
        try:
            stream.close()
        except BaseException:
            pass
        with self._condition:
            self._stream = None

    def _quieten(self, why):
        """Enter silence, saying so once rather than once per retry."""
        with self._condition:
            self._stream = None
        if not self._silent:
            self._silent = True
            self._silent_deadline = perf_counter()
            self._log("warning", f"{why} Running silently and retrying.")
            with self._condition:
                self._notice = (None,)

    def _silence(self):
        """Advance one block with nowhere to put it.

        **The one timer in this file, and it is the exception that proves the
        rule.** Everywhere else the device is the clock. With no device there
        is no clock, and audio time still has to move -- otherwise a dropout
        queues every scheduled write and fires them in a burst the moment a
        device returns, and the engine's inbox grows for as long as the
        silence lasts.

        Paced against a running deadline rather than by sleeping a block at a
        time, because `sleep` overshoots -- 0.24ms median on Windows against
        an 8.33ms block -- and silence that runs three percent slow is still
        wrong.
        """
        self._prepare(self._engine.advance())
        self.silent_blocks += 1
        self._silent_deadline += _BLOCK_SECONDS
        remaining = self._silent_deadline - perf_counter()
        if remaining > 0:
            sleep(remaining)

    def _log(self, level, message):
        if self._logger is not None:
            getattr(self._logger, level)(message)

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
    def device(self) -> str | None:
        """What is being written to, or None while silent."""
        with self._condition:
            return None if self._silent else self._device

    def take_notice(self) -> tuple[str | None] | None:
        """Pop the last device change, for the caller to publish.

        A one-slot mailbox rather than a queue: what a game wants is where
        the audio IS, and an older answer is never the one to act on.
        """
        with self._condition:
            notice, self._notice = self._notice, None
            return notice

    @property
    def silent(self) -> bool:
        """Running, but with no device to write to.

        Not a failure: the graph is live, audio time is moving, and a device
        is being looked for. A game may want to say so on screen.
        """
        with self._condition:
            return self._silent

    @property
    def error(self) -> BaseException | None:
        return self._error

    def stats(self) -> str:
        latency = (
            "unstarted"
            if self._latency is None
            else f"{self._latency * 1000:.1f}ms"
        )
        losses = (
            f"  device_losses={self.device_losses}"
            f"  silent_blocks={self.silent_blocks}"
            if self.device_losses or self.silent_blocks
            else ""
        )
        return (
            f"xruns={self.xruns}"
            f"{losses}"
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
