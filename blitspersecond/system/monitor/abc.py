from abc import ABC, abstractmethod


class Metric(ABC):

    @abstractmethod
    def push(self, sample: float):
        pass

    @property
    @abstractmethod
    def average(self) -> float:
        pass
