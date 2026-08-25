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

from blitspersecond.common import SingletonMeta

#: The stdlib logger name `Logger` wraps. Hard-coded rather than imported so
#: that renaming it in the engine fails a test instead of quietly making
#: these fixtures guard nothing.
ENGINE_LOGGER = "BlitsPerSecond"


@pytest.fixture(autouse=True)
def fresh_singletons():
    """Give each test an empty singleton registry, and leave one behind."""
    SingletonMeta._instances.clear()
    yield
    SingletonMeta._instances.clear()


@pytest.fixture(autouse=True)
def pristine_stdlib_logger():
    """Restore the stdlib logger's level and handlers around each test."""
    logger = getLogger(ENGINE_LOGGER)
    level, handlers = logger.level, list(logger.handlers)
    yield
    logger.setLevel(level)
    logger.handlers[:] = handlers
