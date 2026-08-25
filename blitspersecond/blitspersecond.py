from typing import Callable, Optional


from blitspersecond.common import SingletonMeta
from blitspersecond.system import Logger, Config, EventBus


class BlitsPerSecond(metaclass=SingletonMeta):

    @classmethod
    def current(cls) -> Optional["BlitsPerSecond"]:
        """Return the existing engine, or ``None`` without constructing one."""
        return SingletonMeta._instances.get(cls)

    def __init__(self):
        self._logger = Logger()
        self._config = Config()
        self._event_bus = EventBus()

    @property
    def logger(self) -> Logger:
        return self._logger
