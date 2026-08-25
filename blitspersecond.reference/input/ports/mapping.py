"""The explicit relationship between one input device and one virtual Port."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from blitspersecond.input.internal import InputDevice

if TYPE_CHECKING:
    from .port import Port


@dataclass(frozen=True)
class InputDeviceMapping:
    """One device's unique assignment to a Port."""

    device: InputDevice
    port: Port
