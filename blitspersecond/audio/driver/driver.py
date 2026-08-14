"""Device-paced presentation of one AudioEngine through PortAudio."""

from threading import Condition, Event, Thread
from time import perf_counter, sleep

from numpy import clip, empty, float32

from ..common import FRAME_SIZE, SAMPLE_RATE
from ..engine import AudioEngine

from .devices import default_output_device

_NEW = "new"
_STARTING = "starting"
_RUNNING = "running"
_CLOSING = "closing"
_CLOSED = "closed"
_FAILED = "failed"


def _load_sounddevice():
    import sounddevice

    return sounddevice


class Driver:
    """Let one long-lived output stream drive one AudioEngine.

    Construction performs no device work. ``start()`` opens the selected
    stream on a dedicated worker and waits until PortAudio accepts the first
    engine Frame. There is deliberately no pause or restart lifecycle: once
    started, the stream runs until terminal ``close()`` or device failure.
    """

    def __init__(self, engine: AudioEngine, latency="low"):
        if not isinstance(engine, AudioEngine):
            raise TypeError(f"expected AudioEngine, got {type(engine)}")
        self._engine = engine
        self._latency_request = latency
        self._condition = Condition()
        self._closing = Event()
        self._thread = None
        self._state = _NEW
        self._error = None

        self._latency = None
        self._device = None
        self._stream_blocksize = None
        self._stream_samplerate = None
        self._epoch = None
        self._buffer = empty((FRAME_SIZE, 2), dtype=float32)

        self.xruns = 0
        self.worst_write = 0.0
        self.blocks_written = 0

    def start(self):
        """Open the permanent stream and wait for its first accepted Frame."""
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

            self._engine._running = True
            self._state = _STARTING
            self._thread = Thread(target=self._run, name="audio-driver")
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
        """Terminally release the worker and PortAudio stream."""
        with self._condition:
            thread = self._thread
            if thread is None:
                self._state = _CLOSED
                return
            if self._state not in (_CLOSED, _FAILED):
                self._state = _CLOSING
                self._closing.set()
                self._condition.notify_all()
        thread.join()

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
                blocksize=0,
                latency=self._latency_request,
                device=selector,
            )

            first = self._prepare(self._engine.advance())
            stream.start()
            stream_active = True
            self._epoch = perf_counter()
            self._latency = float(stream.latency)
            self._stream_blocksize = int(stream.blocksize)
            self._stream_samplerate = float(stream.samplerate)
            self._write(stream, first)

            with self._condition:
                self._state = _RUNNING
                self._condition.notify_all()

            while not self._closing.is_set():
                self._write(stream, self._prepare(self._engine.advance()))
        except BaseException as caught:
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
                self._error = error
                self._state = _FAILED if error is not None else _CLOSED
                self._condition.notify_all()

    def _prepare(self, bus):
        frame = bus.current.data
        if frame.shape != (FRAME_SIZE, 2):
            raise ValueError(
                f"AudioEngine returned shape {frame.shape}, "
                f"expected ({FRAME_SIZE}, 2)"
            )
        if frame.dtype != float32:
            raise TypeError(
                f"AudioEngine returned dtype {frame.dtype}, expected float32"
            )
        clip(frame, -1.0, 1.0, out=self._buffer)
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

    @property
    def running(self):
        with self._condition:
            return self._state == _RUNNING

    @property
    def latency(self):
        return self._latency

    @property
    def epoch(self):
        return self._epoch

    @property
    def error(self):
        return self._error

    def stats(self):
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
    """Run the standalone engine for a finite wall-clock duration."""
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
