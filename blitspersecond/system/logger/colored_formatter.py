from logging import Formatter, LogRecord

from colorama import Fore, Style, init


class ColoredFormatter(Formatter):
    """Wraps a record's levelname in colour for console output.

    Colours in place and RESTORES, rather than leaving the record mutated.
    `Logger` builds one record per call and hands it to every handler in
    turn -- console first, then an optional file. Colouring without
    restoring left the ANSI codes on `record.levelname` after the console
    handler returned, so the file handler's plain Formatter inherited them
    and wrote escape sequences into a plain-text log. Handlers run
    sequentially, so a mutation confined to this method's own frame is
    invisible to every one of them.

    Deliberately NOT `copy(record)`, which was tried first and is worse
    here: `copy.copy` reaches into the import system, and this formatter
    still runs during interpreter shutdown -- pyglet's `Window.__del__`
    logs "Surface closing" from there -- where that raises
    `ImportError: sys.meta_path is None` and the message is lost.
    """

    # Initialize colorama once for the class
    init(autoreset=True)

    COLORS = {
        "DEBUG": Fore.CYAN,
        "INFO": Fore.GREEN,
        "WARNING": Fore.YELLOW,
        "ERROR": Fore.RED,
        "CRITICAL": Fore.RED + Style.BRIGHT,
    }

    def format(self, record: LogRecord) -> str:
        levelname = record.levelname
        colour = self.COLORS.get(levelname)
        if colour is None:
            return super().format(record)
        record.levelname = f"{colour}{levelname}{Style.RESET_ALL}"
        try:
            return super().format(record)
        finally:
            record.levelname = levelname
