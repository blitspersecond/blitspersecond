"""The engine exists, there is only ever one of it, and it hands you a logger.

The whole contract this slice makes. `BlitsPerSecond()` is documented as an
idempotent "engine exists" barrier -- calling it is how you GET the engine,
not just how you make one -- so identity across calls is the promise, not an
implementation detail of the metaclass.
"""

from blitspersecond import BlitsPerSecond
from blitspersecond.system import Config, Logger


def test_engine_defines_the_fixed_machine_shape():
    assert (
        BlitsPerSecond.width,
        BlitsPerSecond.height,
        BlitsPerSecond.framerate,
    ) == (640, 360, 60)


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
