"""Shared identity for a concrete input source a Port can map."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from blitspersecond.input.ports import InputDeviceMapping


class InputDevice:
    """A routable input identity.

    This deliberately defines no generic control API: keyboard predicates,
    pointer motion, pad buttons and MIDI messages remain truthful to their
    devices. Ports use the shared identity only for mapping.
    """

    def __init__(self) -> None:
        self._mapping: InputDeviceMapping | None = None

    @property
    def mapping(self) -> InputDeviceMapping | None:
        """This device's one current Port mapping, or None."""
        return self._mapping

    def _map(self, mapping: InputDeviceMapping) -> None:
        if self._mapping is not None:
            raise ValueError("InputDevice is already mapped to a Port")
        self._mapping = mapping

    def _unmap(self, mapping: InputDeviceMapping) -> None:
        if self._mapping is mapping:
            self._mapping = None
