from .image import Image
from .logger import Logger


class ResourceManager(object):
    _resources = {}

    def __init__(self):
        pass

    def get(self, key: str) -> object:
        # handle different resource types here
        if key not in self._resources:
            self._resources[key] = Image(key)
        return self._resources[key]

    def get_image(self, key: str) -> Image:
        # add type hinting for the return value
        return self.get(key)

    def unset(self, key: str) -> None:
        if key in self._resources:
            del self._resources[key]
        else:
            Logger.error(f"Resource for key '{key}' is not loaded.")
            raise KeyError(f"Resource for key '{key}' is not loaded.")

    def __getitem__(self, key: str) -> object:
        if key in self._resources:
            return self._resources[key]
        else:
            Logger.error(f"Resource for key '{key}' is not loaded.")
            raise KeyError(f"Resource for key '{key}' is not loaded.")

    def __setitem__(self, key: str, value: None):
        if value is None:
            self.unset(key)
        else:
            Logger
            raise ValueError("Only 'None' is allowed when unsetting a resource.")

    def __len__(self) -> int:
        return len(self._resources)

    def __iter__(self):
        return iter(self._resources)
