from .environment import environment_flag
from .framerate import fps
from .paths import PACKAGE_ROOT
from .singleton_meta import SingletonMeta

__all__ = [
    "PACKAGE_ROOT",
    "SingletonMeta",
    "environment_flag",
    "fps",
]
