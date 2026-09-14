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


# -- axis calibration -----------------------------------------------------

import os

from blitspersecond.input.pads.evdev import (
    ABS_HAT0X,
    ABS_X,
    ABS_Z,
    AXES,
    AxisRange,
    _read_request,
    axis_ranges,
    open_node,
)

#: The wired Xbox One pad, read from the hardware on 2026-09-11. Three
#: different ranges on ONE device, which is the whole reason this exists.
XBOX_RANGES = {
    ABS_X: AxisRange(-32768, 32767),
    ABS_Z: AxisRange(0, 1023),
    ABS_HAT0X: AxisRange(-1, 1),
}


def test_a_stick_is_neutral_in_the_middle_and_reaches_one_either_way():
    stick = XBOX_RANGES[ABS_X]
    assert stick.centred(0) == 0.0
    assert stick.centred(-32768) == -1.0
    assert stick.centred(32767) == 1.0


def test_the_midpoint_is_integer_so_a_byte_ranged_stick_rests_at_zero():
    # 0..255 is the other common stick encoding and its neutral is 128. A
    # floating midpoint would be 127.5 and put every such pad permanently
    # off-centre.
    assert AxisRange(0, 255).centred(128) == 0.0
    assert AxisRange(-32768, 32767).centred(0) == 0.0


def test_the_halves_scale_separately_because_they_are_not_the_same_size():
    # A signed range has one more step below the midpoint than above it, so a
    # single divisor would stop one direction short of 1.0.
    stick = AxisRange(-32768, 32767)
    assert stick.centred(-32768) == -1.0
    assert stick.centred(32767) == 1.0


def test_a_trigger_rests_at_zero_rather_than_reading_fully_negative():
    # Reading a trigger through centred() is the bug this pair exists to
    # prevent: 0..1023 has a midpoint of 512, so a released trigger would
    # report -1.0 and a game would think it was held.
    trigger = XBOX_RANGES[ABS_Z]
    assert trigger.travel(0) == 0.0
    assert trigger.travel(1023) == 1.0
    assert trigger.centred(0) == -1.0  # exactly the wrong answer


def test_a_hat_is_already_the_three_values_it_should_be():
    hat = XBOX_RANGES[ABS_HAT0X]
    assert [hat.centred(v) for v in (-1, 0, 1)] == [-1.0, 0.0, 1.0]


def test_a_degenerate_range_answers_zero_rather_than_dividing_by_it():
    assert AxisRange(7, 7).centred(7) == 0.0
    assert AxisRange(7, 7).travel(7) == 0.0


def test_values_beyond_the_declared_range_are_clamped():
    stick = AxisRange(-32768, 32767)
    assert stick.centred(-40000) == -1.0
    assert stick.centred(40000) == 1.0
    assert XBOX_RANGES[ABS_Z].travel(2000) == 1.0


def test_the_ioctl_number_is_eviocgabs():
    # _IOR('E', 0x40 + code, sizeof(struct input_absinfo)); pinned because a
    # wrong request number fails silently as "this pad has no axes".
    assert _read_request(0x40 + ABS_X, 24) == 0x80184540


def test_asking_a_pipe_for_axes_is_empty_rather_than_an_explosion():
    # Every axis answers ENOTTY on something that is not an input device, and
    # that is the same path a pad takes for an axis it does not have.
    read_fd, write_fd = os.pipe()
    try:
        assert axis_ranges(read_fd) == {}
    finally:
        os.close(read_fd)
        os.close(write_fd)


def test_gas_and_brake_are_asked_about_precisely_because_most_pads_lack_them():
    # Their presence is the signal `layout_for` branches on: a device that
    # declares them puts its triggers there and its RIGHT STICK on Z/RZ.
    # Not querying them was a real bug for an afternoon -- the discriminant
    # could never fire, because the fact it needed was never read.
    from blitspersecond.input.pads.evdev import ABS_BRAKE, ABS_GAS

    assert ABS_GAS in AXES and ABS_BRAKE in AXES
    assert len(AXES) == 10
    assert ABS_X in AXES and ABS_Z in AXES and ABS_HAT0X in AXES


@pytest.mark.skipif(not os.path.exists("/proc/bus/input/devices"), reason="linux only")
def test_against_whatever_is_actually_plugged_in():
    # Not a fixture: a live check that open + calibrate works on real
    # hardware, skipped when there is none. Every range the kernel reports
    # must be usable, which is the property the fixtures above assume.
    from blitspersecond.input.pads.evdev import discover as live

    for pad in live().values():
        fd = open_node(pad.path)
        try:
            ranges = axis_ranges(fd)
        finally:
            os.close(fd)
        assert ranges, f"{pad.name} reported no axes at all"
        for code, span in ranges.items():
            assert span.maximum >= span.minimum
            assert -1.0 <= span.centred(span.minimum) <= 1.0
            assert 0.0 <= span.travel(span.maximum) <= 1.0


# -- reading reports ------------------------------------------------------
#
# A pipe stands in for an event node: the framing has no idea what is
# underneath it, which is the point of testing it this way.

import struct

from blitspersecond.input.pads.evdev import (
    BTN_SOUTH,
    EV_ABS,
    EV_KEY,
    EV_SYN,
    SYN_DROPPED,
    SYN_REPORT,
    PadReader,
    Report,
    Snapshot,
)

BTN_EAST = 0x131


def events(*triples):
    """Synthetic `struct input_event` bytes, with plausible timestamps."""
    return b"".join(
        struct.pack("llHHi", 1757600000, 123456, kind, code, value)
        for kind, code, value in triples
    )


def reader_on_a_pipe():
    read_fd, write_fd = os.pipe()
    os.set_blocking(read_fd, False)
    return PadReader(read_fd, {}), write_fd


def test_a_report_is_what_the_kernel_called_simultaneous():
    reader, writer = reader_on_a_pipe()
    os.write(writer, events(
        (EV_KEY, BTN_SOUTH, 1),
        (EV_ABS, ABS_X, -32000),
        (EV_SYN, SYN_REPORT, 0),
    ))
    assert reader.read() == (
        Report(((EV_KEY, BTN_SOUTH, 1), (EV_ABS, ABS_X, -32000))),
    )
    reader.close()
    os.close(writer)


def test_an_unfinished_report_waits_rather_than_being_split_across_frames():
    # The kernel says these happened together. Emitting half of them now and
    # half next frame would report as sequential two things that were not.
    reader, writer = reader_on_a_pipe()
    os.write(writer, events((EV_KEY, BTN_SOUTH, 1), (EV_ABS, ABS_X, -32000)))
    assert reader.read() == ()          # nothing complete yet

    os.write(writer, events((EV_SYN, SYN_REPORT, 0)))
    assert reader.read() == (
        Report(((EV_KEY, BTN_SOUTH, 1), (EV_ABS, ABS_X, -32000))),
    )
    reader.close()
    os.close(writer)


def test_the_order_of_reports_is_the_path_the_stick_took():
    # This is the whole reason reports exist rather than one scan per frame:
    # a roll through three positions inside one frame stays three facts.
    reader, writer = reader_on_a_pipe()
    for value in (-32000, -16000, 0):
        os.write(writer, events((EV_ABS, ABS_X, value), (EV_SYN, SYN_REPORT, 0)))
    assert [r.events[0][2] for r in reader.read()] == [-32000, -16000, 0]
    reader.close()
    os.close(writer)


def test_history_before_a_drop_survives_and_everything_after_it_does_not():
    # A pipe answers ENOTTY to both ioctls, so the snapshot here is the empty
    # one. What is being asserted is the SHAPE: valid history, then a
    # wholesale replacement, and nothing in between.
    reader, writer = reader_on_a_pipe()
    os.write(writer, events(
        (EV_KEY, BTN_SOUTH, 1),
        (EV_SYN, SYN_REPORT, 0),
        (EV_KEY, BTN_EAST, 1),       # part of a report that lost its middle
        (EV_SYN, SYN_DROPPED, 0),
        (EV_KEY, BTN_SOUTH, 0),
        (EV_SYN, SYN_REPORT, 0),     # superseded: the snapshot is newer
    ))
    assert reader.read() == (
        Report(((EV_KEY, BTN_SOUTH, 1),)),
        Snapshot(frozenset(), {}),
    )
    assert reader.drops == 1
    reader.close()
    os.close(writer)


def test_the_snapshot_is_taken_after_the_drain_not_at_the_drop():
    # Reports between the drop and now are already reflected in what the
    # device says is true, so replaying them would apply stale history on top
    # of current truth. They go.
    reader, writer = reader_on_a_pipe()
    os.write(writer, events((EV_SYN, SYN_DROPPED, 0)))
    for value in (-32000, -16000, 0):
        os.write(writer, events((EV_ABS, ABS_X, value), (EV_SYN, SYN_REPORT, 0)))
    assert reader.read() == (Snapshot(frozenset(), {}),)
    reader.close()
    os.close(writer)


def test_a_pad_keeps_working_after_a_drop():
    # An earlier draft muted the device permanently once desynced. A drop
    # loses state; it must not lose the pad.
    reader, writer = reader_on_a_pipe()
    os.write(writer, events((EV_SYN, SYN_DROPPED, 0)))
    assert reader.read() == (Snapshot(frozenset(), {}),)

    os.write(writer, events((EV_KEY, BTN_SOUTH, 1), (EV_SYN, SYN_REPORT, 0)))
    assert reader.read() == (Report(((EV_KEY, BTN_SOUTH, 1),)),)
    assert reader.drops == 1
    reader.close()
    os.close(writer)


def test_a_snapshot_can_be_asked_for_without_a_drop():
    # Resume wants exactly this: ticks stopped, the world moved on, and the
    # question is what is true now rather than what happened.
    reader, writer = reader_on_a_pipe()
    assert reader.snapshot() == Snapshot(frozenset(), {})
    assert reader.drops == 0
    reader.close()
    os.close(writer)


def test_event_kinds_the_pad_cannot_use_are_dropped_at_the_transport():
    EV_MSC, EV_FF = 0x04, 0x15
    reader, writer = reader_on_a_pipe()
    os.write(writer, events(
        (EV_MSC, 0x04, 589825),
        (EV_KEY, BTN_SOUTH, 1),
        (EV_FF, 0x50, 1),
        (EV_SYN, SYN_REPORT, 0),
    ))
    assert reader.read() == (Report(((EV_KEY, BTN_SOUTH, 1),)),)
    reader.close()
    os.close(writer)


def test_an_empty_report_is_not_a_report():
    # The kernel emits SYN_REPORT even when nothing this reader cares about
    # changed. An empty one would be a frame's worth of nothing.
    reader, writer = reader_on_a_pipe()
    os.write(writer, events((EV_SYN, SYN_REPORT, 0), (EV_SYN, SYN_REPORT, 0)))
    assert reader.read() == ()
    reader.close()
    os.close(writer)


def test_nothing_to_read_is_an_ordinary_answer():
    reader, writer = reader_on_a_pipe()
    assert reader.read() == ()
    assert reader.read() == ()
    reader.close()
    os.close(writer)


def test_closing_twice_is_safe_because_teardown_runs_on_half_built_things():
    reader, writer = reader_on_a_pipe()
    reader.close()
    reader.close()
    os.close(writer)
