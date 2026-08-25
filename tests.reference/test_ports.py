"""Virtual Port mappings are stable and independent of device discovery."""

import operator

import pytest

import blitspersecond.input as input_api
from blitspersecond.input import (
    InputDeviceMapping,
    Pad,
    Port,
    Ports,
    kbm,
)
from blitspersecond.input.internal import InputDevice
from blitspersecond.input.pads import XboxMapper


def pad(fingerprint: str) -> Pad:
    return Pad(fingerprint, name="pretend pad", mapper=XboxMapper)


def test_ports_and_pads_are_distinct_public_concepts() -> None:
    assert input_api.Ports is Ports
    assert input_api.Port is Port
    assert input_api.InputDeviceMapping is InputDeviceMapping
    assert input_api.Pads is not Ports
    assert not hasattr(input_api, "InputDevice")
    assert issubclass(Pad, InputDevice)


def test_ports_owns_four_stable_ports() -> None:
    ports = Ports()

    assert ports.p1 is ports.p1
    assert ports.p2 is ports.p2
    assert ports.p3 is ports.p3
    assert ports.p4 is ports.p4
    assert len({ports.p1, ports.p2, ports.p3, ports.p4}) == 4
    assert [ports.p1.id, ports.p2.id, ports.p3.id, ports.p4.id] == [1, 2, 3, 4]


def test_connect_creates_the_device_to_port_relationship() -> None:
    port = Port(1)
    device = InputDevice()

    mapping = port.connect(device)

    assert isinstance(mapping, InputDeviceMapping)
    assert mapping.device is device
    assert mapping.port is port
    assert device.mapping is mapping
    assert port.mappings[device] is mapping


def test_a_device_can_map_to_only_one_port() -> None:
    ports = Ports()
    device = pad("pad-one")
    mapping = ports.p1.connect(device)

    with pytest.raises(ValueError, match="already mapped"):
        ports.p2.connect(device)

    assert device.mapping is mapping
    assert mapping.port is ports.p1
    assert device not in ports.p2.mappings


def test_disconnect_allows_an_explicit_remap() -> None:
    ports = Ports()
    device = pad("pad-one")
    first = ports.p1.connect(device)

    ports.p1.disconnect(device)
    second = ports.p2.connect(device)

    assert device.mapping is second
    assert first.port is ports.p1
    assert second.port is ports.p2
    assert device not in ports.p1.mappings


def test_session_teardown_clears_every_device_mapping() -> None:
    ports = Ports()
    first = pad("pad-one")
    second = pad("pad-two")
    ports.p1.connect(first)
    ports.p2.connect(second)

    ports._disconnect_all()

    assert first.mapping is None
    assert second.mapping is None
    assert not ports.p1.mappings
    assert not ports.p2.mappings


def test_disconnect_rejects_a_device_mapped_elsewhere() -> None:
    ports = Ports()
    device = pad("pad-one")
    ports.p1.connect(device)

    with pytest.raises(ValueError, match="not mapped to this Port"):
        ports.p2.disconnect(device)


def test_mappings_cannot_be_mutated_behind_the_port() -> None:
    port = Port(1)
    device = InputDevice()

    with pytest.raises(TypeError):
        operator.setitem(
            port.mappings,
            device,
            InputDeviceMapping(device, port),
        )


def test_port_exposes_the_mapped_keyboard_mouse_device() -> None:
    port = Port(1)
    assert port.keyboard is None
    assert port.mouse is None
    if kbm.mapping is not None:
        kbm.mapping.port.disconnect(kbm)

    try:
        mapping = port.connect(kbm)
        mapped = port.kbm

        assert mapped is kbm
        assert kbm.mapping is mapping
        assert mapped.keyboard is kbm.keyboard
        assert mapped.mouse is kbm.mouse
        assert port.keyboard is kbm.keyboard
        assert port.mouse is kbm.mouse
    finally:
        if kbm.mapping is not None:
            kbm.mapping.port.disconnect(kbm)

    assert port.keyboard is None
    assert port.mouse is None


def test_ports_finds_kbm_facets_wherever_the_device_is_mapped() -> None:
    ports = Ports()
    if kbm.mapping is not None:
        kbm.mapping.port.disconnect(kbm)
    with pytest.raises(RuntimeError, match="no keyboard"):
        _ = ports.keyboard
    with pytest.raises(RuntimeError, match="no mouse"):
        _ = ports.mouse

    try:
        ports.p3.connect(kbm)

        assert ports.keyboard is kbm.keyboard
        assert ports.mouse is kbm.mouse
    finally:
        if kbm.mapping is not None:
            kbm.mapping.port.disconnect(kbm)

    with pytest.raises(RuntimeError, match="no keyboard"):
        _ = ports.keyboard
    with pytest.raises(RuntimeError, match="no mouse"):
        _ = ports.mouse


def test_gamepad_is_the_first_mapped_pad() -> None:
    port = Port(1)
    first = pad("pad-one")
    second = pad("pad-two")

    port.connect(first)
    port.connect(second)

    assert port.gamepad is first


def test_ports_gamepad_is_the_first_connected_mapped_pad() -> None:
    ports = Ports()
    disconnected = pad("disconnected")
    connected = pad("connected")
    disconnected._disconnect()
    ports.p1.connect(disconnected)
    ports.p2.connect(connected)

    assert ports.gamepad is connected

    connected._disconnect()

    assert ports.gamepad is None


def test_ports_gamepad_checks_every_mapping_within_a_port() -> None:
    ports = Ports()
    disconnected = pad("disconnected")
    connected = pad("connected")
    disconnected._disconnect()
    ports.p1.connect(disconnected)
    ports.p1.connect(connected)

    assert ports.gamepad is connected


def test_pad_identity_and_port_identity_are_independent() -> None:
    ports = Ports()
    gamepad = pad("pad-one")
    gamepad._identify(1)

    ports.p3.connect(gamepad)

    assert gamepad.id == 1
    assert gamepad.mapping is not None
    assert gamepad.mapping.port.id == 3


def test_ports_do_not_transfer_a_mapping_between_pad_objects() -> None:
    ports = Ports()
    original = pad("same-fingerprint")
    original._identify(1)
    ports.p3.connect(original)
    original._disconnect()

    replacement = pad("same-fingerprint")
    replacement._identify(1)

    assert replacement.mapping is None
    assert ports.p3.gamepad is original
