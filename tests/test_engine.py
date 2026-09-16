"""The engine exists, there is only ever one of it, and it hands you a logger.

The whole contract this slice makes. `BlitsPerSecond()` is documented as an
idempotent "engine exists" barrier -- calling it is how you GET the engine,
not just how you make one -- so identity across calls is the promise, not an
implementation detail of the metaclass.
"""

import pytest

from blitspersecond import BlitsPerSecond, EngineState, fps
from blitspersecond.audio import AudioEngine, MixingDesk
from blitspersecond.system import Config, Logger


def test_engine_defines_the_fixed_machine_shape():
    """Width and height only. `framerate` used to sit here and no longer does
    -- it became an authoring choice rather than part of the machine."""
    assert (BlitsPerSecond.width, BlitsPerSecond.height) == (640, 360)


def test_framerate_defaults_to_sixty():
    assert BlitsPerSecond().framerate is fps.SIXTY


def test_framerate_cannot_be_changed_afterwards():
    """No setter, deliberately. Every tuned constant in a game is counted in
    frames, so there is no coherent thing a setter could do -- changing the
    rate mid-run would silently redefine jump arcs, input windows and
    animation timing all at once."""
    engine = BlitsPerSecond()
    with pytest.raises(AttributeError):
        engine.framerate = fps.THIRTY


def test_engine_constructs():
    assert isinstance(BlitsPerSecond(), BlitsPerSecond)


def test_engine_is_a_singleton():
    """A second call hands back the FIRST engine, rather than booting one."""
    assert BlitsPerSecond() is BlitsPerSecond()


def test_current_is_none_before_construction():
    """`current()` answers "is there an engine yet" WITHOUT making one.

    The distinction is the whole reason the classmethod exists: a caller who
    only wants to know cannot be the one that boots the display.
    """
    assert BlitsPerSecond.current() is None


def test_current_does_not_construct_an_engine():
    BlitsPerSecond.current()
    assert BlitsPerSecond.current() is None


def test_current_returns_the_engine_once_it_exists():
    engine = BlitsPerSecond()
    assert BlitsPerSecond.current() is engine


def test_engine_exposes_a_logger():
    assert isinstance(BlitsPerSecond().logger, Logger)


def test_engine_exposes_a_config():
    assert isinstance(BlitsPerSecond().config, Config)


def test_engine_logger_is_a_stable_reach_through():
    """`bps.logger` answers the same object every time.

    Neither `Logger` nor `Config` is a singleton itself -- `BlitsPerSecond`
    is, so identity only needs holding ACROSS the one engine, not enforcing
    a second time on the objects it hands out. A bare `Logger()`/`Config()`
    constructed outside the engine is a plain, unrelated object; `bps.logger`
    / `bps.config` is the intended way to reach the engine's own.
    """
    engine = BlitsPerSecond()
    assert engine.logger is engine.logger
    assert BlitsPerSecond().logger is engine.logger


def test_engine_config_is_a_stable_reach_through():
    engine = BlitsPerSecond()
    assert engine.config is engine.config
    assert BlitsPerSecond().config is engine.config


def test_engine_state_is_reachable_from_the_package_root():
    """`bps.state` is compared against the member, never against a string.

    The whole reason the enum is re-exported: without it a game branching on
    engine state has to reach into `blitspersecond.internal` or match on
    `.name`, and both are worse than the thing they work around. It is the
    one internal name that comes out, because it is a vocabulary for reading
    a property rather than machinery a game would construct.
    """
    engine = BlitsPerSecond()
    assert engine.state is EngineState.RUNNING


# -- audio ---------------------------------------------------------------
# The ordering below is the whole subject. Starting the driver FREEZES the
# audio graph, and the window between constructing the engine and calling
# `start()` is exactly when a game builds its voices. Starting it during
# warm-up was tried and broke that: every `bps.audio.source(...)` raised.
# `Loop.run` opens the device instead, after the graph exists and before the
# pacing baselines, so device activation cannot read as a stalled first frame.


def test_the_engine_exposes_one_audio_graph():
    engine = BlitsPerSecond()
    assert isinstance(engine.audio, AudioEngine)
    assert engine.audio is engine.audio


def test_constructing_the_engine_does_not_open_the_audio_device():
    """Warm-up opens the window; the loop opens the device."""
    engine = BlitsPerSecond()
    assert engine._audio_driver.running is False


def test_a_game_can_build_its_audio_graph_before_starting():
    """The regression this section exists for: topology must still be open."""
    engine = BlitsPerSecond()
    desk = engine.audio.output
    assert isinstance(desk, MixingDesk)
    voice = engine.audio.source()
    desk.connect(voice)

    assert desk.registers_for(voice)["level"] == 1.0


def test_the_audio_graph_is_frozen_once_the_device_is_driven():
    engine = BlitsPerSecond()
    engine.audio._running = True
    try:
        with pytest.raises(RuntimeError):
            engine.audio.source()
    finally:
        engine.audio._running = False


def test_teardown_releases_the_audio_worker():
    engine = BlitsPerSecond()
    engine._teardown()
    assert engine.audio._running is False
