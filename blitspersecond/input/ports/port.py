"""One virtual input Port and the devices mapped to it."""

from __future__ import annotations

from types import MappingProxyType
from typing import Mapping, Optional

from blitspersecond.input.internal import InputDevice
from blitspersecond.input.kbm import Keyboard, KeyboardMouse, Mouse
from blitspersecond.input.pads import Pad

from .mapping import InputDeviceMapping


class Port:
    """A stable virtual input station.

    A Port owns mappings, not devices. The same InputDevice cannot be mapped
    to two Ports because the device retains its one current mapping.
    """

    def __init__(self, id: int) -> None:
        if id < 1:
            raise ValueError("Port id must be positive")
        self._id = id
        self._mappings: dict[InputDevice, InputDeviceMapping] = {}

    @property
    def id(self) -> int:
        """This Port's stable, one-based identity."""
        return self._id

    @property
    def mappings(self) -> Mapping[InputDevice, InputDeviceMapping]:
        """The devices mapped here and their explicit Port relationships."""
        return MappingProxyType(self._mappings)

    @property
    def kbm(self) -> Optional[KeyboardMouse]:
        return next(
            (
                device
                for device in self._mappings
                if isinstance(device, KeyboardMouse)
            ),
            None,
        )

    @property
    def keyboard(self) -> Optional[Keyboard]:
        """The mapped KBM's keyboard facet, or None."""
        kbm = self.kbm
        return None if kbm is None else kbm.keyboard

    @property
    def mouse(self) -> Optional[Mouse]:
        """The mapped KBM's pointer facet, or None."""
        kbm = self.kbm
        return None if kbm is None else kbm.mouse

    @property
    def gamepad(self) -> Optional[Pad]:
        """The first mapped Pad; twin-stick/multi-pad policy remains open."""
        return next(
            (
                device
                for device in self._mappings
                if isinstance(device, Pad)
            ),
            None,
        )

    def connect(self, device: InputDevice) -> InputDeviceMapping:
        """Map one currently-unmapped device to this Port."""
        if not isinstance(device, InputDevice):
            raise TypeError(f"expected InputDevice, got {type(device).__name__}")
        if device.mapping is not None:
            raise ValueError("InputDevice is already mapped to a Port")
        if isinstance(device, KeyboardMouse) and self.kbm is not None:
            raise ValueError("Port already has a keyboard and mouse mapped")

        mapping = InputDeviceMapping(device, self)
        device._map(mapping)
        self._mappings[device] = mapping
        return mapping

    def disconnect(self, device: InputDevice) -> None:
        """Remove this Port's mapping for a device."""
        mapping = self._mappings.pop(device, None)
        if mapping is None:
            raise ValueError("InputDevice is not mapped to this Port")
        device._unmap(mapping)

    def _disconnect_all(self) -> None:
        for device in tuple(self._mappings):
            self.disconnect(device)
