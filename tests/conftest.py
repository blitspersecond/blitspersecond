"""Test isolation for the engine's process-global state.

Two different globals sit under this first slice, and both leak between
tests unless they are put back:

  * `SingletonMeta._instances` is ONE dict shared by every singleton class,
    so a `BlitsPerSecond()` in an early test decides whether a later test
    constructs anything at all. Without the reset, `test_engine_constructs`
    and `test_current_is_none_before_construction` cannot both be true, and
    every singleton assertion in the suite passes trivially forever.

  * the stdlib logger `getLogger("BlitsPerSecond")` OUTLIVES the reset above.
    Clearing the singleton mints a new `Logger` object over the same stdlib
    logger, and `Logger.__init__` guards handler setup with
    `if not self.logger.handlers` -- so the second `Logger()` in a process
    skips setup entirely and inherits whatever level and handlers the
    previous test left behind. A test calling `set_level(DEBUG)` would
    silently turn on DEBUG for everything after it.

Both fixtures are autouse: this is state you cannot opt out of, so opting
in would only be a way to forget.
"""

from logging import getLogger

import pytest

from blitspersecond import BlitsPerSecond
from blitspersecond.common import SingletonMeta

#: The stdlib logger name `Logger` wraps. Hard-coded rather than imported so
#: that renaming it in the engine fails a test instead of quietly making
#: these fixtures guard nothing.
ENGINE_LOGGER = "BlitsPerSecond"


def _close_any_engine() -> None:
    """Close the display a test's engine opened, if it made one.

    Clearing the registry only FORGETS the engine -- it does not close the
    window, which then survives until the garbage collector happens to run
    pyglet's ``__del__``. Constructing an engine opens a display (warm-up
    blocks in ``__init__``), and eleven tests here do, so without this the
    windows pile up mid-run -- three were observed open at once -- and
    pyglet's teardown logging fires after the runner has closed its handlers.

    Deliberately NOT ``engine.stop()``. Stop is terminal: with no loop
    servicing the lifecycle it runs the entire shutdown and calls
    ``sys.exit``, which would take the test runner down with it.
    ``_teardown()`` brings the subsystems down without touching the state
    machine, which is all a finished test needs.
    """
    engine = BlitsPerSecond.current()
    if engine is not None:
        engine._teardown()


@pytest.fixture(autouse=True)
def fresh_singletons(pristine_stdlib_logger):
    """Give each test an empty singleton registry, and leave one behind.

    Depends on `pristine_stdlib_logger` purely for ORDERING. Closing a
    display logs ("Surface closing."), and a fixture's teardown runs before
    that of anything it depends on -- so requesting it here puts the engine
    close BEFORE the handler restore. The other way round, that log line
    lands on a stdlib logger whose handlers have just been emptied, so
    `Logger.__init__`'s `if not self.logger.handlers` guard fires and leaves
    a console handler behind. The next test then constructs `Logger(file=...)`,
    sees handlers already present, skips adding the file handler, and its log
    file is never created.
    """
    _close_any_engine()
    SingletonMeta._instances.clear()
    yield
    _close_any_engine()
    SingletonMeta._instances.clear()


@pytest.fixture(autouse=True)
def pristine_stdlib_logger():
    """Restore the stdlib logger's level and handlers around each test."""
    logger = getLogger(ENGINE_LOGGER)
    level, handlers = logger.level, list(logger.handlers)
    yield
    logger.setLevel(level)
    logger.handlers[:] = handlers
