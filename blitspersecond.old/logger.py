import logging
from .config import Config


class Logger:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            # Configure the logger
            logging.basicConfig(
                level=logging.DEBUG,
                format="%(asctime)s - %(levelname)s - %(message)s",
            )
            cls._instance.logger = logging.getLogger(__name__)
        return cls._instance

    def __init__(self):
        super().__init__()
        if Config().default.debug:
            logging.basicConfig(
                level=logging.WARNING,
                format="%(asctime)s - %(levelname)s - %(message)s",
            )
        else:
            logging.basicConfig(
                level=logging.DEBUG, format="%(asctime)s - %(levelname)s - %(message)s"
            )
        logging.getLogger("PIL").setLevel(
            logging.WARNING
        )  # Suppress DEBUG and INFO messages from PIL

    def debug(self, message):
        self.logger.debug(message)

    def info(self, message):
        self.logger.info(message)

    def warning(self, message):
        self.logger.warning(message)

    def error(self, message):
        self.logger.error(message)

    def critical(self, message):
        self.logger.critical(message)
