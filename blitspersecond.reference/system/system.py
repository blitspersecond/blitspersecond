from blitspersecond.system.monitor import Logger
from blitspersecond.common import SingletonMeta

from blitspersecond.system.config import Config


class System(metaclass=SingletonMeta):
    def __init__(self):
        self._logger = Logger()
        self._config = Config()

    @property
    def logger(self) -> Logger:
        return self._logger

    @property
    def config(self) -> Config:
        return self._config
