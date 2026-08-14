"""The audio-driver boundary, exercised without an OS audio device."""

from threading import get_ident
from time import sleep

import numpy as np
import pytest

from blitspersecond.audio.common import FRAME_SIZE
from blitspersecond.audio.driver import Driver, render
from blitspersecond.audio.driver import driver as driver_module
from blitspersecond.audio.engine import AudioEngine, MixingDesk, Program
from blitspersecond.audio.operators import Operator


class Loud(Operator):
    def process(self, bus):
        bus.current.data.fill(2.0)
        return bus


class Level(Operator):
    def __init__(self, level):
        self.level = level

    def process(self, bus):
        bus.current.data.fill(self.level)
        return bus


def engine_with_output():
    engine = AudioEngine()
    source = engine.source(Program(Loud))
    output = engine.mixing_desk()
    output.connect(source)
    engine.output = output
    return engine


class FakeStream:
    def __init__(self, owner, **kwargs):
        self.owner = owner
        self.kwargs = kwargs
        self.latency = 0.008
        self.blocksize = 0
        self.samplerate = float(kwargs["samplerate"])
        owner.stream = self
        owner.record("construct")

    def start(self):
        self.owner.record("start")

    def write(self, block):
        self.owner.record("write")
        self.owner.blocks.append(block.copy())
        sleep(0.0005)
        return False

    def stop(self):
        self.owner.record("stop")

    def close(self):
        self.owner.record("close")


class FakeSounddevice:
    def __init__(self):
        self.calls = []
        self.blocks = []
        self.stream = None

    def record(self, operation):
        self.calls.append((operation, get_ident()))

    def query_devices(self, device=None, kind=None):
        self.record("query_devices")
        assert device is None
        assert kind == "output"
        return {"name": "Fake output", "hostapi": 0}

    def query_hostapis(self, index=None):
        self.record("query_hostapis")
        assert index == 0
        return {"name": "Fake PortAudio"}

    def OutputStream(self, **kwargs):
        return FakeStream(self, **kwargs)


def install_fake_driver(monkeypatch, fake):
    monkeypatch.setattr(driver_module, "_load_sounddevice", lambda: fake)
    monkeypatch.setattr(
        driver_module,
        "default_output_device",
        lambda _sounddevice: None,
    )


def test_worker_drives_one_engine_until_terminal_close(monkeypatch):
    fake = FakeSounddevice()
    install_fake_driver(monkeypatch, fake)
    engine = engine_with_output()
    driver = Driver(engine)
    main_thread = get_ident()

    driver.start()
    assert driver.running
    assert engine._running
    driver.start()  # Starting the one permanent stream is idempotent.
    driver.close()

    assert not driver.running
    assert not engine._running
    assert driver.error is None
    assert driver.blocks_written >= 1
    assert engine.clock == driver.blocks_written * FRAME_SIZE
    assert fake.stream.kwargs == {
        "samplerate": 48_000,
        "channels": 2,
        "dtype": "float32",
        "blocksize": 0,
        "latency": "low",
        "device": None,
    }
    assert all(block.shape == (FRAME_SIZE, 2) for block in fake.blocks)
    assert all(np.allclose(block, 1.0) for block in fake.blocks)
    worker_threads = {thread for _, thread in fake.calls}
    assert len(worker_threads) == 1
    assert main_thread not in worker_threads
    assert [call for call, _ in fake.calls].count("start") == 1
    assert [call for call, _ in fake.calls].count("stop") == 1
    assert [call for call, _ in fake.calls].count("close") == 1
    assert driver.xruns == 0
    with pytest.raises(RuntimeError, match="cannot restart"):
        driver.start()


def test_driver_preserves_native_stereo_channels():
    engine = AudioEngine()
    left = engine.source(Program(lambda: Level(0.25)))
    right = engine.source(Program(lambda: Level(2.0)))
    desk = engine.mixing_desk()
    desk.connect(left)
    desk.connect(right)
    engine.output = desk
    engine.schedule(
        [
            (0, desk, left, {"pan": -1.0}),
            (0, desk, right, {"pan": 1.0}),
        ]
    )

    block = Driver(engine)._prepare(engine.advance())

    assert np.all(block[:, 0] == 0.25)
    assert np.all(block[:, 1] == 1.0)


def test_driver_rejects_a_mono_terminal_stage():
    engine = AudioEngine()
    engine.output = engine.source(Program(lambda: Level(0.25)))

    with pytest.raises(ValueError, match=r"expected \(400, 2\)"):
        Driver(engine)._prepare(engine.advance())


def test_only_one_driver_can_claim_an_engine(monkeypatch):
    fake = FakeSounddevice()
    install_fake_driver(monkeypatch, fake)
    engine = engine_with_output()
    first = Driver(engine)
    second = Driver(engine)

    first.start()
    with pytest.raises(RuntimeError, match="already driven"):
        second.start()
    first.close()


def test_startup_failure_releases_engine(monkeypatch):
    class BrokenSounddevice(FakeSounddevice):
        def OutputStream(self, **kwargs):
            raise OSError("no output")

    fake = BrokenSounddevice()
    install_fake_driver(monkeypatch, fake)
    engine = engine_with_output()
    driver = Driver(engine)

    with pytest.raises(RuntimeError, match="no output"):
        driver.start()
    driver.close()

    assert not engine._running
    assert driver.error is not None


def test_driver_package_exposes_engine_and_transport_as_separate_surfaces():
    import blitspersecond.audio as audio
    import blitspersecond.audio.common as common
    import blitspersecond.audio.driver as driver
    import blitspersecond.audio.engine as engine
    import blitspersecond.audio.operators as operators

    assert audio.__all__ == []
    assert common.FRAME_SIZE == FRAME_SIZE
    assert driver.Driver is Driver
    assert driver.__all__ == (
        "Driver",
        "play",
        "render",
        "render_wav",
        "write_wav",
    )
    assert engine.AudioEngine is AudioEngine
    assert engine.MixingDesk is MixingDesk
    assert operators.Operator is Operator
    assert not hasattr(audio, "AudioEngine")
    assert not hasattr(driver, "OutputDevice")
    assert not hasattr(engine, "Operator")


def test_offline_and_device_drivers_cannot_own_one_engine_together():
    engine = AudioEngine()
    engine._running = True

    with pytest.raises(RuntimeError, match="already driven"):
        render(engine, 1.0)
