"""The engine's four stable virtual input Ports."""

from blitspersecond.input.kbm import Keyboard, Mouse
from blitspersecond.input.pads import Pad

from .port import Port


class Ports:
    """Four Ports, independent of physical discovery order."""

    def __init__(self) -> None:
        self._ports: tuple[Port, Port, Port, Port] = (
            Port(1),
            Port(2),
            Port(3),
            Port(4),
        )

    @property
    def p1(self) -> Port:
        return self._ports[0]

    @property
    def p2(self) -> Port:
        return self._ports[1]

    @property
    def p3(self) -> Port:
        return self._ports[2]

    @property
    def p4(self) -> Port:
        return self._ports[3]

    @property
    def keyboard(self) -> Keyboard:
        """Return the mapped keyboard facet."""
        for port in self._ports:
            if port.keyboard is not None:
                return port.keyboard
        raise RuntimeError("no keyboard is mapped to a Port")

    @property
    def mouse(self) -> Mouse:
        """Return the mapped mouse facet."""
        for port in self._ports:
            if port.mouse is not None:
                return port.mouse
        raise RuntimeError("no mouse is mapped to a Port")

    @property
    def gamepad(self) -> Pad | None:
        """The first connected Pad mapped across P1-P4, or None."""
        for port in self._ports:
            for device in port.mappings:
                if isinstance(device, Pad) and device.connected:
                    return device
        return None

    def _disconnect_all(self) -> None:
        for port in self._ports:
            port._disconnect_all()
