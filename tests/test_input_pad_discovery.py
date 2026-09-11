"""Finding pads, and deciding what each connection is called.

The blocks below are real, captured from `/proc/bus/input/devices` on
2026-09-11 with three pads, a keyboard and a headset attached. Parsing captured
text rather than the live file is what makes "my pad is not detected"
reproducible without the pad.

See `docs/bps/PADS_PORT.md`, *Identity: measured, not assumed*.
"""

import pytest

from blitspersecond.input.pads import DualSenseMapper, XboxMapper
from blitspersecond.input.pads.evdev import discover

WIRED_XBOX = """\
I: Bus=0003 Vendor=045e Product=02d1 Version=0203
N: Name="Microsoft X-Box One pad"
P: Phys=usb-0000:2f:00.3-1.3.3/input0
U: Uniq=
H: Handlers=event29 js3 
B: KEY=7cdb000000000000 0 0 0 0
B: ABS=3003f"""

DUALSENSE = """\
I: Bus=0005 Vendor=054c Product=0ce6 Version=8100
N: Name="DualSense Wireless Controller"
P: Phys=
U: Uniq=a0:fa:9c:22:70:bb
H: Handlers=event31 js5 
B: KEY=7fdb000000000000 0 0 0 0
B: ABS=3003f"""

# Same Bluetooth adapter as the headset below -- that is the point of it.
XBOX_WIRELESS = """\
I: Bus=0005 Vendor=045e Product=0b13 Version=0523
N: Name="Xbox Wireless Controller"
P: Phys=ac:67:5d:a5:5d:8d
U: Uniq=14:cb:65:88:75:1c
H: Handlers=event30 js4 
B: KEY=7cdb000000000000 0 0 0 0
B: ABS=3003f"""

KEYBOARD = """\
I: Bus=0003 Vendor=05ac Product=0250 Version=0111
N: Name="Keychron Keychron K3"
P: Phys=usb-0000:2f:00.3-1.3.1/input0
U: Uniq=
H: Handlers=sysrq kbd leds event13 
B: KEY=10000 0 0 0 101007b02001007 ff9f207ac14057ff ffbeffdfffefffff fffffffffffffffe"""

HEADSET = """\
I: Bus=0005 Vendor=0000 Product=0000 Version=0000
N: Name="MAJOR III BLUETOOTH (AVRCP)"
P: Phys=ac:67:5d:a5:5d:8d
U: Uniq=
H: Handlers=kbd event2 
B: KEY=1 0 0 0 0"""


def proc(*blocks):
    return "\n\n".join(blocks) + "\n"


def test_bluetooth_is_named_by_the_device_because_there_is_no_socket():
    found = discover(proc(DUALSENSE))
    assert list(found) == ["bt:a0:fa:9c:22:70:bb"]
    pad = found["bt:a0:fa:9c:22:70:bb"]
    assert pad.model == "DualSense"
    assert pad.mapper is DualSenseMapper


def test_wired_is_named_by_the_port_it_is_plugged_into():
    found = discover(proc(WIRED_XBOX))
    assert list(found) == ["port:usb-0000:2f:00.3-1.3.3/input0"]
    pad = found["port:usb-0000:2f:00.3-1.3.3/input0"]
    assert pad.model == "Xbox One Controller"
    assert pad.mapper is XboxMapper


def test_two_bluetooth_pads_on_one_adapter_stay_two_pads():
    # THE test. Both report the SAME phys -- it is the host adapter -- so a
    # rule that preferred the physical field would merge them and one would
    # silently overwrite the other. Naming a Bluetooth connection by the
    # device's own MAC is what keeps them apart.
    found = discover(proc(DUALSENSE, XBOX_WIRELESS))
    assert len(found) == 2
    assert set(found) == {
        "bt:a0:fa:9c:22:70:bb",
        "bt:14:cb:65:88:75:1c",
    }


def test_the_shared_adapter_is_never_what_a_bluetooth_pad_is_called():
    found = discover(proc(XBOX_WIRELESS, HEADSET))
    # The headset reports the identical phys and is not a pad; the pad is
    # named by its own MAC, so neither can reach the other's identity.
    assert list(found) == ["bt:14:cb:65:88:75:1c"]
    assert not any("ac:67:5d:a5:5d:8d" in key for key in found)


def test_a_bluetooth_pad_with_no_mac_is_refused_rather_than_merged():
    nameless = XBOX_WIRELESS.replace("U: Uniq=14:cb:65:88:75:1c", "U: Uniq=")
    assert discover(proc(nameless)) == {}


def test_a_device_with_neither_a_port_nor_a_mac_is_refused():
    nowhere = DUALSENSE.replace("U: Uniq=a0:fa:9c:22:70:bb", "U: Uniq=")
    assert discover(proc(nowhere)) == {}


def test_steam_is_refused_by_vendor_before_identity_is_even_asked():
    # Its virtual pads present every controller as one vendor:product, so
    # there is nothing to tell them apart with. Built here as a pad that WOULD
    # otherwise be nameable, to prove the vendor check comes first.
    virtual = WIRED_XBOX.replace("Vendor=045e Product=02d1", "Vendor=28de Product=11ff")
    assert discover(proc(virtual)) == {}


def test_only_gamepad_shaped_nodes_are_offered():
    found = discover(proc(KEYBOARD, WIRED_XBOX, HEADSET))
    assert len(found) == 1
    assert found["port:usb-0000:2f:00.3-1.3.3/input0"].name == (
        "Microsoft X-Box One pad"
    )


def test_the_event_node_is_captured_but_is_not_the_identity():
    # The path moves across a replug; the fingerprint does not. A pad
    # re-enumerating as event40 is the same pad.
    moved = WIRED_XBOX.replace("event29", "event40")
    before = discover(proc(WIRED_XBOX))
    after = discover(proc(moved))
    assert list(before) == list(after)
    assert before["port:usb-0000:2f:00.3-1.3.3/input0"].path == "/dev/input/event29"
    assert after["port:usb-0000:2f:00.3-1.3.3/input0"].path == "/dev/input/event40"


def test_a_pad_moved_to_another_socket_is_a_different_pad():
    # The N64 rule, and the accepted cost of it: swapping cables swaps
    # players. Per-session identity does not have to survive rewiring a desk.
    moved = WIRED_XBOX.replace("1.3.3/input0", "1.3.7/input0")
    assert list(discover(proc(WIRED_XBOX))) != list(discover(proc(moved)))


def test_an_unknown_pad_is_offered_with_no_name_and_the_fallback_mapper():
    generic = WIRED_XBOX.replace("Vendor=045e Product=02d1", "Vendor=1234 Product=5678")
    pad = next(iter(discover(proc(generic)).values()))
    assert pad.model is None
    assert pad.name == "Microsoft X-Box One pad"  # the transport's own string
    assert pad.mapper is XboxMapper


def test_a_node_with_no_event_handler_cannot_be_opened_so_is_not_offered():
    handlerless = WIRED_XBOX.replace("H: Handlers=event29 js3 ", "H: Handlers=js3 ")
    assert discover(proc(handlerless)) == {}


def test_an_unreadable_procfs_is_empty_rather_than_an_exception():
    # A machine with no /proc is a machine with no pads, not a crash.
    assert discover("") == {}


@pytest.mark.parametrize("blocks", [(), (KEYBOARD,), (HEADSET, KEYBOARD)])
def test_no_pads_is_an_ordinary_answer(blocks):
    assert discover(proc(*blocks) if blocks else "") == {}
