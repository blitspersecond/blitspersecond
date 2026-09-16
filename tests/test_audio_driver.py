"""`Driver`: the readiness handshake, the claim, and terminal close.

**No hardware.** `_load_sounddevice` is the seam the reference left for
exactly this, so these drive a fake PortAudio whose stream records what it was
given. What is under test is the lifecycle and the contract at the device
boundary, and neither needs a sound card to be wrong.

**The worker is a thread, so every test must be able to fail rather than
hang.** The fake stream's `write` is what paces the loop here, and it is held
by an Event the test controls: nothing waits on a real device, and a driver
that never reaches RUNNING fails its wait instead of blocking the suite.
"""

import threading

import numpy as np
import pytest

from blitspersecond.audio.transport import driver as driver_module
from blitspersecond.audio.constants import BLOCK_SIZE, SAMPLE_RATE
from blitspersecond.audio.transport.driver import Driver, play
from blitspersecond.audio.synth.engine import AudioEngine
from blitspersecond.audio.synth.mixing_desk import MixingDesk
from blitspersecond.audio.synth.operators import Operator
from blitspersecond.audio.synth.program import Program


class _Stream:
    """A PortAudio stream that records, and never blocks for long."""

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.latency = 0.01
        self.blocksize = 0
        self.samplerate = kwargs.get("samplerate")
        self.started = False
        self.stopped = False
        self.closed = False
        self.writes = []
        self.underflow = False
        self.fail_on_write = None
        self.aborted = False

    def start(self):
        self.started = True

    def write(self, block):
        if self.fail_on_write is not None and len(self.writes) >= self.fail_on_write:
            raise RuntimeError("device went away")
        self.writes.append(np.array(block))
        return self.underflow

    def stop(self):
        self.stopped = True

    def abort(self):
        self.aborted = True

    def close(self):
        self.closed = True


class _SoundDevice:
    """Just enough of the module for the worker to get a stream open."""

    def __init__(self):
        self.stream: _Stream | None = None
        self.raise_on_open = None

    def query_devices(self, selector, kind=None):
        return {"name": "Fake Output", "hostapi": 0}

    def query_hostapis(self, index=None):
        if index is None:
            return [{"name": "Windows WASAPI", "default_output_device": 0}]
        return {"name": "FakeAPI"}

    def OutputStream(self, **kwargs):
        if self.raise_on_open is not None:
            raise self.raise_on_open
        self.stream = _Stream(**kwargs)
        return self.stream


def _desk(engine) -> MixingDesk:
    """`AudioEngine.output` is typed `Stage` because it is replaceable,
    so the obvious `engine.output.connect(...)` needs narrowing."""
    output = engine.output
    assert isinstance(output, MixingDesk)
    return output


class _Tone(Operator):
    def process(self, bus, /):
        bus.current.data.fill(0.5)
        bus.current.quiescent = False
        return bus


@pytest.fixture
def fake(monkeypatch) -> _SoundDevice:
    module = _SoundDevice()
    monkeypatch.setattr(driver_module, "_load_sounddevice", lambda: module)
    # Device-policy contracts have their own topology tests. Driver tests use
    # a transport fake and should not vary with the host running the suite.
    monkeypatch.setattr(driver_module, "default_output_device", lambda _: None)
    return module


@pytest.fixture
def engine() -> AudioEngine:
    engine = AudioEngine()
    _desk(engine).connect(engine.source(Program(_Tone)))
    return engine


def _started(engine, fake):
    driver = Driver(engine)
    driver.start()
    return driver


# -- the readiness handshake -------------------------------------------


def test_start_returns_only_once_a_block_has_been_accepted(engine, fake):
    driver = _started(engine, fake)
    try:
        assert driver.running is True
        assert fake.stream is not None
        assert fake.stream.started is True
        assert len(fake.stream.writes) >= 1
    finally:
        driver.close()


def test_the_first_block_is_rendered_before_the_stream_starts(engine, fake):
    """The device is never asked for audio that does not exist yet."""
    order = []

    class _Watching(_Stream):
        def start(self):
            order.append("stream.start")
            super().start()

    class _Probe(Operator):
        def process(self, bus, /):
            order.append("render")
            bus.current.quiescent = False
            return bus

    fake.OutputStream = lambda **kw: _Watching(**kw)
    _desk(engine).connect(engine.source(Program(_Probe)))

    driver = _started(engine, fake)
    try:
        assert order[:2] == ["render", "stream.start"]
    finally:
        driver.close()


def test_the_stream_is_opened_at_the_engine_rate_in_stereo(engine, fake):
    driver = _started(engine, fake)
    try:
        kwargs = fake.stream.kwargs
        assert kwargs["samplerate"] == SAMPLE_RATE
        assert kwargs["channels"] == 2
        assert kwargs["dtype"] == "float32"
        # The host chooses its own period; ours is not imposed on it.
        assert kwargs["blocksize"] == 0
    finally:
        driver.close()


def test_a_device_that_will_not_open_fails_the_caller(engine, fake):
    """Not hidden in a background thread with silence to explain itself."""
    fake.raise_on_open = OSError("no such device")
    driver = Driver(engine)

    with pytest.raises(RuntimeError, match="no such device"):
        driver.start()

    assert driver.running is False
    assert engine._running is False


def test_a_failed_driver_cannot_be_started_again(engine, fake):
    fake.raise_on_open = OSError("no such device")
    driver = Driver(engine)
    with pytest.raises(RuntimeError):
        driver.start()
    with pytest.raises(RuntimeError):
        driver.start()


# -- claiming the engine ------------------------------------------------


def test_a_running_driver_owns_the_engine(engine, fake):
    driver = _started(engine, fake)
    try:
        assert engine._running is True
        with pytest.raises(RuntimeError):
            engine.source()
    finally:
        driver.close()


def test_two_drivers_cannot_own_one_engine(engine, fake):
    first = _started(engine, fake)
    try:
        with pytest.raises(RuntimeError, match="already driven"):
            Driver(engine).start()
    finally:
        first.close()


def test_an_offline_render_cannot_claim_a_playing_engine(engine, fake):
    from blitspersecond.audio.transport.offline import render

    driver = _started(engine, fake)
    try:
        with pytest.raises(RuntimeError):
            render(engine, 0.01)
    finally:
        driver.close()


def test_starting_twice_is_idempotent(engine, fake):
    driver = _started(engine, fake)
    try:
        driver.start()
        assert driver.running is True
    finally:
        driver.close()


# -- closing -------------------------------------------------------------


def test_close_stops_the_stream_and_releases_the_engine(engine, fake):
    driver = _started(engine, fake)
    driver.close()

    assert driver.running is False
    assert engine._running is False
    assert fake.stream.stopped is True
    assert fake.stream.closed is True


def test_close_is_terminal(engine, fake):
    driver = _started(engine, fake)
    driver.close()
    with pytest.raises(RuntimeError, match="cannot restart"):
        driver.start()


def test_closing_an_unstarted_driver_is_harmless(engine, fake):
    Driver(engine).close()


def test_close_is_idempotent(engine, fake):
    driver = _started(engine, fake)
    driver.close()
    driver.close()


def test_close_aborts_a_write_that_will_not_return(engine, fake):
    entered = threading.Event()
    released = threading.Event()

    class _Blocking(_Stream):
        def write(self, block):
            if self.writes:
                entered.set()
                released.wait()
            return super().write(block)

        def abort(self):
            super().abort()
            released.set()

    def output_stream(**kwargs):
        fake.stream = _Blocking(**kwargs)
        return fake.stream

    fake.OutputStream = output_stream
    driver = _started(engine, fake)
    assert entered.wait(0.5)

    closing = threading.Thread(target=driver.close, daemon=True)
    closing.start()
    closing.join(0.5)
    if closing.is_alive():
        # Keep a failed test from leaving its worker behind or hanging pytest.
        released.set()
        closing.join(0.5)

    assert not closing.is_alive()
    assert fake.stream.aborted is True
    assert driver.error is None


def test_a_device_failing_mid_stream_releases_the_engine(engine, fake):
    driver = Driver(engine)
    driver.start()
    fake.stream.fail_on_write = 1
    for _ in range(200):
        if not driver.running:
            break
        threading.Event().wait(0.005)
    driver.close()

    assert engine._running is False
    assert isinstance(driver.error, RuntimeError)


# -- the device boundary --------------------------------------------------


def test_a_mono_output_is_refused_at_the_boundary(fake):
    """The driver never manufactures a second channel from one."""
    engine = AudioEngine()
    engine.output = engine.source(Program(_Tone))
    driver = Driver(engine)

    with pytest.raises(RuntimeError, match="expected"):
        driver.start()


def test_blocks_are_clipped_on_the_way_out(fake):
    class _Loud(Operator):
        def process(self, bus, /):
            bus.current.data.fill(4.0)
            bus.current.quiescent = False
            return bus

    engine = AudioEngine()
    _desk(engine).connect(engine.source(Program(_Loud)))
    driver = _started(engine, fake)
    try:
        assert fake.stream.writes[0].max() <= 1.0
        assert fake.stream.writes[0].shape == (BLOCK_SIZE, 2)
    finally:
        driver.close()


def test_the_output_buffer_is_reused_rather_than_allocated(engine, fake):
    driver = _started(engine, fake)
    try:
        first = driver._buffer
        assert driver._prepare(engine._output.process()) is first
    finally:
        driver.close()


def test_a_driver_needs_an_engine():
    with pytest.raises(TypeError):
        Driver(object())  # type: ignore[arg-type]


# -- diagnostics ----------------------------------------------------------


def test_underflows_are_counted(engine, fake):
    driver = Driver(engine)
    driver.start()
    try:
        fake.stream.underflow = True
        for _ in range(200):
            if driver.xruns:
                break
            threading.Event().wait(0.005)
        assert driver.xruns > 0
    finally:
        driver.close()


def test_stats_name_the_device_and_the_reported_latency(engine, fake):
    driver = _started(engine, fake)
    try:
        assert "FakeAPI: Fake Output" in driver.stats()
        assert driver.latency == pytest.approx(0.01)
        assert driver.epoch is not None
    finally:
        driver.close()


def test_an_unstarted_driver_reports_no_latency(engine):
    driver = Driver(engine)
    assert driver.latency is None
    assert driver.epoch is None
    assert "unstarted" in driver.stats()


# -- play ------------------------------------------------------------------


def test_play_runs_and_closes(engine, fake):
    driver = play(engine, 0.01)
    assert driver.running is False
    assert engine._running is False
    assert driver.blocks_written > 0


def test_play_refuses_a_negative_duration(engine, fake):
    with pytest.raises(ValueError):
        play(engine, -1.0)
