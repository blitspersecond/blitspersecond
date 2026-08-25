from enum import Enum
from logging import (CRITICAL, DEBUG, ERROR, INFO, WARNING, FileHandler,
                     Formatter, StreamHandler, getLogger)
from pathlib import Path
from sys import stdout
from typing import Optional

from colorama import Fore, Style, init

from blitspersecond.common import SingletonMeta


class LogLevel(Enum):
    DEBUG = DEBUG
    INFO = INFO
    WARNING = WARNING
    ERROR = ERROR
    CRITICAL = CRITICAL

    @classmethod
    def from_string(cls, level_str: str):
        """Convert string to LogLevel, defaulting to INFO if invalid"""
        try:
            return cls[level_str.upper()]
        except KeyError:
            return cls.INFO


class ColoredFormatter(Formatter):
    """Custom formatter that adds color to console logs"""

    # Initialize colorama once for the class
    init(autoreset=True)

    COLORS = {
        "DEBUG": Fore.CYAN,
        "INFO": Fore.GREEN,
        "WARNING": Fore.YELLOW,
        "ERROR": Fore.RED,
        "CRITICAL": Fore.RED + Style.BRIGHT,
    }

    def format(self, record):
        # Save original levelname
        levelname = record.levelname
        # Add color to levelname based on log level
        if levelname in self.COLORS:
            record.levelname = f"{self.COLORS[levelname]}{levelname}{Style.RESET_ALL}"
        return super().format(record)


class Logger(metaclass=SingletonMeta):
    """A singleton logger that wraps the Python logging module"""

    # TODO: refactor this; do we want a singleton really? if so then we shouldn't be
    # passing params into the constructor - remember python loggers are hierarchical
    def __init__(
        self,
        name: str = "BlitsPerSecond",
        level: LogLevel = LogLevel.INFO,
        file: Optional[str] = None,
    ) -> None:
        """Initialize the logger with console and optional file output"""
        self.logger = getLogger(name)
        self.logger.setLevel(level.value)

        # Prevent adding handlers if already initialized
        if not self.logger.handlers:
            # Set up formatting
            standard_formatter = Formatter(
                "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )

            # Colored formatter for console
            colored_formatter = ColoredFormatter(
                "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )

            # Console handler with colored output
            console = StreamHandler(stdout)
            console.setFormatter(colored_formatter)
            self.logger.addHandler(console)

            # Optional file handler
            if file:
                # Create directory if it doesn't exist
                log_path = Path(file).parent
                log_path.mkdir(parents=True, exist_ok=True)

                file_handler = FileHandler(file)
                file_handler.setFormatter(standard_formatter)
                self.logger.addHandler(file_handler)

    def set_level(self, level: LogLevel) -> None:
        """Set the minimum logging level"""
        if not isinstance(level, LogLevel):
            raise TypeError(
                f"Log level must be a LogLevel enum, got {type(level).__name__}"
            )
        self.logger.setLevel(level.value)

    def debug(self, message: str) -> None:
        """Log a debug message"""
        self.logger.debug(message)

    def info(self, message: str) -> None:
        """Log an info message"""
        self.logger.info(message)

    def warning(self, message: str) -> None:
        """Log a warning message"""
        self.logger.warning(message)

    def error(self, message: str) -> None:
        """Log an error message"""
        self.logger.error(message)

    def critical(self, message: str) -> None:
        """Log a critical message"""
        self.logger.critical(message)

    def exception(self, message: str) -> None:
        """Log an exception with traceback"""
        self.logger.exception(message)
