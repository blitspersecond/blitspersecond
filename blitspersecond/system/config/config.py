from dataclasses import dataclass, field
import os

from blitspersecond.common import SingletonMeta, Size


def _environment_flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None or not raw:
        return default
    if raw.casefold() in {"1", "true", "yes", "on"}:
        return True
    if raw.casefold() in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean flag")


@dataclass
class Display:
    height: int = 360
    width: int = 640
    fps: int = 60
    scale: int = 4
    # Borderless at the current desktop mode; never switches display modes.
    # Presentation uses the largest centred integer scale that fits.
    fullscreen: bool = field(
        default_factory=lambda: _environment_flag("BPS_FULLSCREEN")
    )
    # Unthrottled-fallback pacing: spin-poll the last ~2ms to the present
    # deadline (exact grid, costs up to ~a quarter core -- fine on a desktop).
    # False keeps the deadline grid but sleeps the whole way: drift-free,
    # only sleep-wake jitter remains. Never spins when vsync is blocking.
    spin_pacing: bool = True


@dataclass
class Input:
    poll_rate: int = 60


@dataclass
class Tiles:
    max: int = field(
        default_factory=lambda: Display().height // 10 * Display().width // 20
    )
    default: Size = Size(32, 32)


class Config(metaclass=SingletonMeta):
    def __init__(self):
        self._display = Display()
        self._input = Input()
        self._tiles = Tiles()

    @property
    def display(self):
        return self._display

    @property
    def input(self):
        return self._input

    @property
    def tiles(self):
        return self._tiles
