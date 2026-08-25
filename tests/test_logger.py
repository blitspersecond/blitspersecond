"""The logger works: it emits, it filters, and the console output is coloured.

Split in two halves, because two different things can break. `caplog` sees
RECORDS -- what was emitted and at what level -- and says nothing about how
they are rendered. The rendering is `ColoredFormatter`'s job and is tested
directly against a record, since the colour never reaches a record.
"""

from logging import DEBUG, LogRecord, WARNING

import pytest
from colorama import Fore, Style

from blitspersecond.system import Logger
from blitspersecond.system.logger.colored_formatter import ColoredFormatter
from blitspersecond.system.logger.logger import LogLevel

from .conftest import ENGINE_LOGGER

#: Every level the Logger has a method for, paired with the numeric level a
#: record carries when it goes out. `exception` is deliberately absent -- it
#: is ERROR plus a traceback and gets its own test.
LEVELS = [
    ("debug", DEBUG),
    ("info", 20),
    ("warning", WARNING),
    ("error", 40),
    ("critical", 50),
]


class TestEmitting:
    def test_logger_is_not_a_singleton(self):
        """`BlitsPerSecond` is the singleton; `bps.logger` is the intended
        way to reach one (see test_engine.py). A bare `Logger()` is just a
        normal object, so two calls give two DIFFERENT wrapper instances."""
        assert Logger() is not Logger()

    def test_same_name_shares_the_underlying_stdlib_logger(self):
        """What still makes repeat construction safe without a singleton:
        `logging.getLogger(name)` is its own global registry keyed on name,
        so every `Logger()` wraps the SAME stdlib logger regardless of how
        many wrapper instances exist -- which is also why the `if not
        self.logger.handlers` guard in `Logger.__init__` never sees a second
        construction as empty and never double-adds a handler."""
        assert Logger().logger is Logger().logger

    @pytest.mark.parametrize("method, level", LEVELS)
    def test_each_level_emits_one_record_at_that_level(self, caplog, method, level):
        # Order matters, and it is the engine's behaviour rather than a test
        # quirk: `Logger()` sets its own level in __init__, so a capture level
        # asked for beforehand is overwritten. Open the gate through the
        # engine's own API, which is how a caller would do it anyway.
        caplog.set_level(DEBUG)
        logger = Logger()
        logger.set_level(LogLevel.DEBUG)
        getattr(logger, method)("the message")

        assert len(caplog.records) == 1
        record = caplog.records[0]
        assert record.levelno == level
        assert record.getMessage() == "the message"
        assert record.name == ENGINE_LOGGER

    def test_exception_records_the_traceback(self, caplog):
        """`exception()` is ERROR that carries exc_info -- the traceback is
        the entire reason to call it over `error()`."""
        caplog.set_level(DEBUG)
        try:
            raise ValueError("boom")
        except ValueError:
            Logger().exception("while doing the thing")

        record = caplog.records[0]
        assert record.levelno == 40
        assert record.exc_info is not None
        assert isinstance(record.exc_info[1], ValueError)


class TestFiltering:
    def test_defaults_to_info(self):
        assert Logger().logger.level == LogLevel.INFO.value

    def test_debug_is_dropped_at_the_default_level(self, caplog):
        """Nothing is captured, and it is dropped AT THE LOGGER -- so a debug
        call in a hot path costs a level comparison, not a formatted record."""
        Logger().debug("not this one")
        assert caplog.records == []

    def test_set_level_admits_what_it_was_dropping(self, caplog):
        caplog.set_level(DEBUG)  # the capture handler, not the engine logger
        logger = Logger()
        logger.set_level(LogLevel.DEBUG)
        logger.debug("now visible")

        assert [r.getMessage() for r in caplog.records] == ["now visible"]

    def test_set_level_refuses_a_bare_int(self):
        """The guard exists because `logging`'s own levels ARE ints, so
        `set_level(10)` is the plausible wrong call, not an absurd one."""
        with pytest.raises(TypeError):
            Logger().set_level(DEBUG)  # pyright: ignore[reportArgumentType]


class TestLogLevel:
    def test_from_string_is_case_insensitive(self):
        assert LogLevel.from_string("warning") is LogLevel.WARNING
        assert LogLevel.from_string("WARNING") is LogLevel.WARNING

    def test_from_string_falls_back_to_info(self):
        """Configuration is a place a typo should not stop the engine."""
        assert LogLevel.from_string("chatty") is LogLevel.INFO


class TestConsoleColour:
    """`ColoredFormatter` is what makes the console readable at a glance, and
    it is invisible to `caplog` -- the colour goes into the formatted string,
    never onto the record. So it is exercised against a record directly."""

    FORMAT = "%(levelname)s|%(message)s"

    @staticmethod
    def _record(level: int, levelname: str) -> LogRecord:
        record = LogRecord(ENGINE_LOGGER, level, "f.py", 1, "the message", None, None)
        record.levelname = levelname
        return record

    @pytest.mark.parametrize(
        "levelname, colour",
        [
            ("DEBUG", Fore.CYAN),
            ("INFO", Fore.GREEN),
            ("WARNING", Fore.YELLOW),
            ("ERROR", Fore.RED),
            ("CRITICAL", Fore.RED + Style.BRIGHT),
        ],
    )
    def test_the_levelname_is_wrapped_in_its_colour(self, levelname, colour):
        formatted = ColoredFormatter(self.FORMAT).format(
            self._record(WARNING, levelname)
        )
        assert formatted == f"{colour}{levelname}{Style.RESET_ALL}|the message"

    def test_the_message_itself_is_left_uncoloured(self):
        """Only the level is colour-coded. Colouring the message would put
        escape codes through anything that greps the output."""
        formatted = ColoredFormatter(self.FORMAT).format(
            self._record(WARNING, "WARNING")
        )
        assert formatted.endswith("|the message")

    def test_an_unknown_levelname_passes_through_uncoloured(self):
        """Custom levels are not an error -- they are just not colour-coded."""
        formatted = ColoredFormatter(self.FORMAT).format(self._record(35, "NOTICE"))
        assert formatted == "NOTICE|the message"

    def test_formatting_leaves_the_record_untouched(self):
        """Regression: `format()` used to colour `record.levelname` IN
        PLACE, so the ANSI codes were still there for whichever handler ran
        next. One record is shared across every handler `Logger` has --
        console, then an optional file -- so a mutated record leaked escape
        sequences into a plain-text log file. Colouring must happen on a
        copy, leaving the record every later handler sees exactly as built.
        """
        record = self._record(WARNING, "WARNING")
        ColoredFormatter(self.FORMAT).format(record)
        assert record.levelname == "WARNING"

    def test_a_file_handler_after_the_console_handler_sees_no_colour(self, tmp_path):
        """End-to-end guard for the same regression, through the real
        Logger wiring rather than the formatter alone."""
        from blitspersecond.system.logger.logger import Logger

        log_file = tmp_path / "bps.log"
        Logger(file=str(log_file)).warning("disk is filling up")

        assert b"\x1b[" not in log_file.read_bytes()
