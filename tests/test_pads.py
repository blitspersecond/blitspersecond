"""Headless pad tests: the logic has no GL and no hardware in the loop.

Discovery and mapping are pure functions of /proc text and vendor ids, and a
Pad's snapshot is a pure function of the events fed to it -- so we build Pads
directly, feed them, and read the queries. No pad needs to be plugged in, and
nothing here opens a window. (Hardware checks are a separate thing: a timed
probe with a human mashing. See docs/CONTROLLERS.md.)
"""

import errno
from collections import deque
from io import StringIO
from math import hypot
import struct

import pytest

import blitspersecond.input.pads.evdev as evdev_module
from blitspersecond.input.pads import (
    Button,
    DualSenseMapper,
    NintendoMapper,
    Pad,
    Pads,
    SonyMapper,
    XboxMapper,
)
from blitspersecond.input.pads.codes import (
    ABS_HAT0X,
    ABS_HAT0Y,
    ABS_RX,
    ABS_RY,
    ABS_RZ,
    ABS_X,
    ABS_Y,
    BTN_EAST,
    BTN_SOUTH,
    BTN_START,
    BTN_THUMBL,
    EV_ABS,
    EV_KEY,
    EV_SYN,
    SYN_DROPPED,
    SYN_REPORT,
)
from blitspersecond.input.pads.mappers import mapper_for, model_for
from blitspersecond.input.pads.evdev import (
    _ABS_INFO_FMT,
    _FMT,
    Device,
    Evdev,
    Report,
    Snapshot,
    key_state,
)
from blitspersecond.input.pads.pad import _AxisRange

FP = "uniq:aa:bb:cc:dd:ee:ff"


def pad(
    mapper=XboxMapper,
    fingerprint=FP,
    name="pretend pad",
    model=None,
    axis_ranges=None,
):
    return Pad(
        fingerprint,
        name=name,
        mapper=mapper,
        model=model,
        axis_ranges=axis_ranges,
    )


def tick(p, *events):
    """One tick: open it, feed raw (etype, code, value) events, as Pads does."""
    p._ticks += 1
    p._begin_tick(p._ticks)
    for etype, code, value in events:
        p._feed(etype, code, value)


def pads_with(*pads):
    """A Pads collection holding pre-made pads, with no devices open."""
    p = Pads()
    p._pads = list(pads)
    p._by_fingerprint = {x.fingerprint: x for x in pads}
    p._known_by_fingerprint = dict(p._by_fingerprint)
    return p


def wire_events(*events):
    """Pack (type, code, value) tuples as Linux input_event records."""
    return b"".join(struct.pack(_FMT, 0, 0, *event) for event in events)


# -- the selector ------------------------------------------------------------


@pytest.mark.parametrize(
    "vendor,product,expected",
    [
        ("045e", "0b13", XboxMapper),  # Xbox Series
        ("054c", "05c4", SonyMapper),  # DualShock 4
        ("054c", "0ce6", DualSenseMapper),  # DualSense: model beats vendor
        ("057e", "2009", NintendoMapper),  # Switch Pro
        ("0f0d", "0150", XboxMapper),  # Hori proves the Xbox fallback
    ],
)
def test_selector_picks_the_named_mapper(vendor, product, expected):
    assert mapper_for(vendor, product) is expected


def test_selector_is_not_a_gate():
    """An unknown pad is an Xbox pad -- the duck test, which is the whole
    reason the table can stay small."""
    assert mapper_for("046d", "c21d") is XboxMapper  # unknown Logitech
    assert model_for("046d", "c21d") is None  # no editorial name, not an error


def test_known_model_can_use_the_unknown_vendor_fallback():
    """A curated name does not invent a mapping family the pad does not need."""
    assert model_for("0f0d", "0150") == "Hori Fighting Commander (Xbox)"
    assert mapper_for("0f0d", "0150") is XboxMapper


def test_unknown_model_falls_back_to_its_vendor():
    """An unreleased Sony pad still says 'cross': that is a fact about Sony,
    not about the model."""
    assert mapper_for("054c", "ffff") is SonyMapper
    assert model_for("054c", "ffff") is None


def test_case_insensitive():
    assert mapper_for("054C", "0CE6") is DualSenseMapper
    assert model_for("054C", "0CE6") == "DualSense"


def test_scan_keeps_gamepad_nodes_and_rejects_siblings_and_steam(monkeypatch):
    gamepad_key_mask = "1000000000000 0 0 0 0"  # BTN_SOUTH (0x130)
    proc = f"""
I: Bus=0005 Vendor=054c Product=0ce6 Version=0100
N: Name="Wireless Controller"
P: Phys=aa:bb:cc:dd:ee:ff
U: Uniq=11:22:33:44:55:66
H: Handlers=event9 js0
B: EV=20000b
B: KEY={gamepad_key_mask}

I: Bus=0005 Vendor=054c Product=0ce6 Version=0100
N: Name="Wireless Controller Motion Sensors"
H: Handlers=event10
B: EV=9

I: Bus=0003 Vendor=28de Product=11ff Version=0111
N: Name="Microsoft X-Box 360 pad 0"
H: Handlers=event11 js1
B: EV=20000b
B: KEY={gamepad_key_mask}
"""
    evdev = Evdev(deque())
    monkeypatch.setattr(Evdev, "_byid_serials", staticmethod(lambda: {}))
    monkeypatch.setattr("builtins.open", lambda _path: StringIO(proc))

    found = evdev._scan()

    assert list(found) == ["uniq:11:22:33:44:55:66"]
    device = found["uniq:11:22:33:44:55:66"]
    assert device.path == "/dev/input/event9"
    assert device.name == "Wireless Controller"
    assert device.model == "DualSense"
    assert device.mapper is DualSenseMapper


# -- labels ------------------------------------------------------------------


def test_same_position_different_plastic():
    """The point of naming positions: one Button, three vendor labels."""
    assert XboxMapper.label(Button.FACE_SOUTH) == "A"
    assert SonyMapper.label(Button.FACE_SOUTH) == "cross"
    assert NintendoMapper.label(Button.FACE_SOUTH) == "B"


def test_nintendo_letters_are_rotated_not_renamed():
    """A/B/X/Y all exist on both pads -- in different places. This is exactly
    why Button refuses to speak in letters."""
    assert NintendoMapper.label(Button.FACE_EAST) == "A"
    assert XboxMapper.label(Button.FACE_EAST) == "B"


def test_dualsense_only_differs_from_ds4_where_sony_changed_it():
    assert SonyMapper.label(Button.SELECT) == "Share"
    assert DualSenseMapper.label(Button.SELECT) == "Create"
    assert DualSenseMapper.label(Button.FACE_NORTH) == SonyMapper.label(
        Button.FACE_NORTH
    )


def test_label_falls_back_to_the_position_name():
    class Bare(XboxMapper):
        LABELS = {}

    assert Bare.label(Button.FACE_SOUTH) == "FACE_SOUTH"


def test_pad_labels_through_its_own_mapper():
    assert pad(NintendoMapper).label(Button.FACE_SOUTH) == "B"
    assert pad(NintendoMapper).label(Button.START) == "+"


def test_accept_uses_each_pad_familys_conventional_confirm_button():
    xbox = pad(XboxMapper)
    nintendo = pad(NintendoMapper)

    tick(xbox, (EV_KEY, BTN_SOUTH, 1))
    tick(nintendo, (EV_KEY, BTN_EAST, 1))

    assert xbox.pressed(Button.ACCEPT)
    assert nintendo.pressed(Button.ACCEPT)
    assert xbox.label(Button.ACCEPT) == "A"
    assert nintendo.label(Button.ACCEPT) == "A"


# -- a pad's snapshot --------------------------------------------------------


def test_press_and_release_edges():
    p = pad()
    tick(p, (EV_KEY, BTN_SOUTH, 1))
    assert p.pressed(Button.FACE_SOUTH)
    assert p.held(Button.FACE_SOUTH)
    assert not p.released(Button.FACE_SOUTH)

    tick(p)  # a tick with no events: the edge is gone, the hold remains
    assert not p.pressed(Button.FACE_SOUTH)
    assert p.held(Button.FACE_SOUTH)

    tick(p, (EV_KEY, BTN_SOUTH, 0))
    assert p.released(Button.FACE_SOUTH)
    assert not p.held(Button.FACE_SOUTH)


def test_button_predicates_compose_with_or():
    p = pad()
    condition = Button.START | Button.ACCEPT

    tick(p, (EV_KEY, BTN_SOUTH, 1))

    assert p.pressed(condition)
    assert repr(condition) == "Button.START | Button.ACCEPT"


def test_accept_predicate_does_not_pollute_physical_button_iteration():
    assert Button.ACCEPT not in tuple(Button)
    assert all(isinstance(button, Button) for button in Button)


def test_autorepeat_is_not_an_edge():
    p = pad()
    tick(p, (EV_KEY, BTN_SOUTH, 1))
    tick(p, (EV_KEY, BTN_SOUTH, 2))  # kernel autorepeat
    assert not p.pressed(Button.FACE_SOUTH)
    assert p.held(Button.FACE_SOUTH)


def test_held_for_counts_ticks_not_wall_time():
    p = pad()
    tick(p, (EV_KEY, BTN_START, 1))
    assert p.held_for(Button.START) == 0  # 0 on the press tick
    for expected in (1, 2, 3):
        tick(p)
        assert p.held_for(Button.START) == expected


def test_stick_clicks_are_unbindable():
    """L3/R3 are absent from the vocabulary on purpose; their codes go nowhere
    rather than producing a Button a game could bind."""
    p = pad()
    tick(p, (EV_KEY, BTN_THUMBL, 1))
    assert not any(p.held(b) for b in Button)


def test_trigger_thresholds_into_button_edges():
    """The generic Xbox path handles the Hori's digitally duplicated RT."""
    p = pad(XboxMapper)
    tick(p, (EV_ABS, ABS_RZ, 255))
    assert p.pressed(Button.RT)

    tick(p, (EV_ABS, ABS_RZ, 0))
    assert p.released(Button.RT)
    assert not p.held(Button.RT)


def test_trigger_at_the_threshold_is_not_a_press():
    p = pad()
    tick(p, (EV_ABS, ABS_RZ, 30))
    assert not p.held(Button.RT)


def test_trigger_just_past_the_xinput_threshold_is_a_press():
    p = pad()
    tick(p, (EV_ABS, ABS_RZ, 31))
    assert p.pressed(Button.RT)


def test_digital_stick_uses_exact_normalised_positions():
    """The Hori's stick needs no special mapper: its axis is simply digital."""
    p = pad(XboxMapper)

    tick(p, (EV_ABS, ABS_X, -32768))
    assert p.axis(ABS_X) == -1.0
    assert p.left_stick() == (-1.0, 0.0)

    tick(p, (EV_ABS, ABS_X, 0))
    assert p.axis(ABS_X) == 0.0
    assert p.left_stick() == (0.0, 0.0)

    tick(p, (EV_ABS, ABS_X, 32767))
    assert p.axis(ABS_X) == 1.0
    assert p.left_stick() == (1.0, 0.0)

    assert p.right_stick() == (0.0, 0.0)  # an absent right stick is neutral


def test_dualshock_unsigned_stick_uses_its_evdev_range():
    """Linux reports a DS4 stick as 0..255, centred at 128."""
    p = pad(
        SonyMapper,
        axis_ranges={
            ABS_X: _AxisRange(0, 255),
            ABS_Y: _AxisRange(0, 255),
        },
    )

    tick(p, (EV_ABS, ABS_X, 128), (EV_ABS, ABS_Y, 128))
    assert p.left_stick() == (0.0, 0.0)

    tick(p, (EV_ABS, ABS_X, 0))
    assert p.axis(ABS_X) == -1.0
    assert p.left_stick() == (-1.0, 0.0)

    tick(p, (EV_ABS, ABS_X, 255))
    assert p.axis(ABS_X) == 1.0
    assert p.left_stick() == (1.0, 0.0)


def test_unseen_unsigned_axis_is_neutral_not_its_raw_zero_minimum():
    p = pad(axis_ranges={ABS_X: _AxisRange(0, 255)})

    assert p.axis(ABS_X) == 0.0


def test_sticks_apply_their_xinput_circular_deadzones():
    p = pad()

    tick(p, (EV_ABS, ABS_X, 7849), (EV_ABS, ABS_RX, 8689))
    assert p.left_stick() == (0.0, 0.0)
    assert p.right_stick() == (0.0, 0.0)

    tick(p, (EV_ABS, ABS_X, 7850), (EV_ABS, ABS_RX, 8690))
    assert p.left_stick()[0] > 0.0
    assert p.right_stick()[0] > 0.0


def test_stick_deadzone_is_radial_and_rescales_the_remaining_travel():
    p = pad()
    tick(p, (EV_ABS, ABS_RX, 32767), (EV_ABS, ABS_RY, -32768))

    x, y = p.right_stick()

    assert hypot(x, y) == pytest.approx(1.0)
    assert x == pytest.approx(2**-0.5)
    assert y == pytest.approx(-(2**-0.5))


# -- directions --------------------------------------------------------------


def test_dpad_reads_as_numpad():
    p = pad()
    assert p.direction() == 5  # neutral
    tick(p, (EV_ABS, ABS_HAT0Y, -1))  # evdev y is up-negative; numpad up is 8
    assert p.direction() == 8
    tick(p, (EV_ABS, ABS_HAT0X, -1))
    assert p.direction() == 7


def test_stick_reads_as_a_direction_past_the_deadzone():
    p = pad()
    tick(p, (EV_ABS, ABS_X, 30000), (EV_ABS, ABS_Y, 30000))
    assert p.direction() == 3  # down-right


def test_stick_inside_the_deadzone_is_neutral():
    p = pad()
    tick(p, (EV_ABS, ABS_X, 7849))
    assert p.direction() == 5


def test_stick_just_outside_the_xinput_deadzone_is_a_direction():
    p = pad()
    tick(p, (EV_ABS, ABS_X, 7850))
    assert p.direction() == 6


def test_dpad_wins_over_the_stick():
    p = pad()
    tick(p, (EV_ABS, ABS_X, 30000), (EV_ABS, ABS_HAT0X, -1))
    assert p.direction() == 4


# -- naming ------------------------------------------------------------------


def test_editorial_name_beats_the_kernel_string():
    p = pad(name="Generic X-Box pad", model="Hori Fighting Commander (Xbox)")
    assert p.name == "Hori Fighting Commander (Xbox)"
    assert p.kernel_name == "Generic X-Box pad"


def test_unknown_pads_keep_the_kernel_string():
    p = pad(name="Some Bluetooth Thing")
    assert p.name == "Some Bluetooth Thing"
    assert p.kernel_name == "Some Bluetooth Thing"


# -- disconnect: the contract the port-indexed API could not deliver ---------


def test_disconnect_force_releases_held_buttons():
    """A game watching released() must see its stop condition, never a stuck
    button, when a battery dies mid-hold. THIS is why a Pad is an object: the
    old API filed this edge under a fingerprint it dropped from the port list
    in the same breath, so nothing could ever read it."""
    p = pad()
    tick(p, (EV_KEY, BTN_SOUTH, 1))
    assert p.held(Button.FACE_SOUTH)

    p._disconnect()
    assert p.released(Button.FACE_SOUTH)  # readable, on the tick it died
    assert not p.held(Button.FACE_SOUTH)
    assert not p.connected


def test_a_disconnected_pad_goes_quiet_and_stays_quiet():
    """The release edge is real for exactly one tick, then the pad is inert --
    it must not report released() forever after."""
    p = pad()
    tick(p, (EV_KEY, BTN_SOUTH, 1))
    p._disconnect()
    assert p.released(Button.FACE_SOUTH)

    tick(p)  # Pads gives a retired pad one last tick to clear its edges
    assert not p.released(Button.FACE_SOUTH)
    assert not p.held(Button.FACE_SOUTH)
    assert p.direction() == 5
    assert p.axis(ABS_X) == 0.0


def test_pads_retires_a_vanished_pad_but_keeps_its_edges_this_tick():
    """End-to-end through the bus: the pad drops off the list, and a game
    still holding it sees the release on the disconnect tick."""
    p = pad()
    pads = pads_with(p)
    pads._queue.append((FP, EV_KEY, BTN_SOUTH, 1))
    pads.update()
    assert p.held(Button.FACE_SOUTH)

    pads._relist({})  # the pad vanished from /proc
    assert list(pads) == []
    assert p.released(Button.FACE_SOUTH)  # the game's stop condition
    assert not p.connected

    pads.update()  # next tick: the retired pad's edge is cleared
    assert not p.released(Button.FACE_SOUTH)


# -- the bus -----------------------------------------------------------------


def test_reader_buffers_complete_kernel_reports_atomically():
    pads = Pads()
    evdev = Evdev(pads._queue)
    pending = []

    desynced = evdev._buffer_chunk(
        FP,
        7,
        wire_events(
            (EV_KEY, BTN_SOUTH, 1),
            (EV_ABS, ABS_X, 20_000),
            (EV_SYN, SYN_REPORT, 0),
        ),
        pending,
        False,
    )

    assert not desynced
    assert not pending
    assert list(pads._queue) == [
        Report(
            FP,
            (
                (EV_KEY, BTN_SOUTH, 1),
                (EV_ABS, ABS_X, 20_000),
            ),
        )
    ]


def test_syn_dropped_discards_the_bad_tail_and_resynchronises(monkeypatch):
    pads = Pads()
    evdev = Evdev(pads._queue)
    pending = []
    snapshot = Snapshot(FP, bytes(0x300 // 8), {ABS_X: 12_345})
    monkeypatch.setattr(evdev, "_snapshot", lambda fd, fingerprint: snapshot)

    desynced = evdev._buffer_chunk(
        FP,
        7,
        wire_events(
            (EV_KEY, BTN_SOUTH, 1),  # current report becomes unreliable
            (EV_SYN, SYN_DROPPED, 0),
            (EV_KEY, BTN_START, 1),  # ignored through the next report boundary
        ),
        pending,
        False,
    )

    assert desynced
    assert not pending
    assert not pads._queue

    desynced = evdev._buffer_chunk(
        FP,
        7,
        wire_events(
            (EV_KEY, BTN_START, 0),  # still ignored after a split read
            (EV_SYN, SYN_REPORT, 0),
            (EV_KEY, BTN_EAST, 1),  # following report is trustworthy again
            (EV_SYN, SYN_REPORT, 0),
        ),
        pending,
        desynced,
    )

    assert not desynced
    assert list(pads._queue) == [
        snapshot,
        Report(FP, ((EV_KEY, BTN_EAST, 1),)),
    ]


def test_resync_snapshot_queries_current_keys_and_supported_axes(monkeypatch):
    def ioctl(fd, request, buffer):
        assert fd == 7
        number = request & 0xFF
        if number == 0x18:  # EVIOCGKEY
            buffer[BTN_SOUTH // 8] |= 1 << (BTN_SOUTH % 8)
        elif number == 0x40 + ABS_X:  # EVIOCGABS(ABS_X)
            struct.pack_into(
                _ABS_INFO_FMT, buffer, 0, 12_345, -32_768, 32_767, 0, 0, 0
            )
        else:
            raise OSError(errno.EINVAL, "unsupported axis")
        return 0

    monkeypatch.setattr("blitspersecond.input.pads.evdev._device_ioctl", ioctl)

    snapshot = Evdev(deque())._snapshot(7, FP)

    assert key_state(snapshot.keys, BTN_SOUTH) == 1
    assert key_state(snapshot.keys, BTN_START) == 0
    assert snapshot.axes == {ABS_X: 12_345}


def test_axis_ranges_come_from_evdev_abs_info(monkeypatch):
    def ioctl(_fd, request, buffer):
        number = request & 0xFF
        if number == 0x40 + ABS_X:
            struct.pack_into(
                _ABS_INFO_FMT, buffer, 0, 128, 0, 255, 0, 0, 0
            )
        else:
            raise OSError(errno.EINVAL, "unsupported axis")

    monkeypatch.setattr("blitspersecond.input.pads.evdev._device_ioctl", ioctl)

    assert Evdev._axis_ranges(7) == {ABS_X: _AxisRange(0, 255)}


def test_resync_snapshot_releases_stale_buttons_and_recentres_axes():
    p = pad()
    tick(p, (EV_KEY, BTN_SOUTH, 1), (EV_ABS, ABS_X, 30_000))
    pads = pads_with(p)
    pads._queue.append(Snapshot(FP, bytes(0x300 // 8), {ABS_X: 0}))

    pads.update()

    assert p.released(Button.FACE_SOUTH)
    assert not p.held(Button.FACE_SOUTH)
    assert p.axis(ABS_X) == 0.0


def test_pads_enumerates_pads():
    a, b = pad(fingerprint="uniq:11"), pad(fingerprint="uniq:22")
    pads = pads_with(a, b)
    assert list(pads) == [a, b]
    assert len(pads) == 2
    assert a.id is None and b.id is None
    assert pads


def test_each_pad_reads_through_its_own_mapper():
    """Two pads, two mappers, one bus -- routing is per fingerprint."""
    sony = pad(SonyMapper, fingerprint="uniq:11")
    nintendo = pad(NintendoMapper, fingerprint="uniq:22")
    pads = pads_with(sony, nintendo)
    pads._queue.append(("uniq:11", EV_KEY, BTN_SOUTH, 1))
    pads._queue.append(("uniq:22", EV_KEY, BTN_SOUTH, 1))
    pads.update()
    assert sony.pressed(Button.FACE_SOUTH) and nintendo.pressed(Button.FACE_SOUTH)
    assert sony.label(Button.FACE_SOUTH) == "cross"
    assert nintendo.label(Button.FACE_SOUTH) == "B"


def test_pads_are_independent():
    a, b = pad(fingerprint="uniq:11"), pad(fingerprint="uniq:22")
    pads = pads_with(a, b)
    pads._queue.append(("uniq:11", EV_KEY, BTN_SOUTH, 1))
    pads.update()
    assert a.pressed(Button.FACE_SOUTH)
    assert not b.pressed(Button.FACE_SOUTH)


def test_identify_until_completes_with_the_pad_that_pressed_the_predicate():
    first = pad(fingerprint="uniq:11")
    second = pad(fingerprint="uniq:22")
    pads = pads_with(first, second)
    identification = pads.identify().until(Button.START | Button.ACCEPT)
    pads._queue.append(("uniq:22", EV_KEY, BTN_START, 1))

    pads.update()

    assert identification.completed
    assert not identification.listening
    assert identification.value is second
    assert second.id == 1
    assert pads[1] is second


def test_bare_identify_accepts_the_first_deliberate_activity():
    first = pad(fingerprint="uniq:11")
    second = pad(fingerprint="uniq:22")
    pads = pads_with(first, second)
    identification = pads.identify()
    pads._queue.append(("uniq:22", EV_ABS, ABS_HAT0X, 1))
    pads._queue.append(("uniq:11", EV_KEY, BTN_START, 1))

    pads.update()

    assert identification.completed
    assert identification.value is second
    assert second.id == 1
    assert first.id == 2


def test_identify_until_ignores_other_deliberate_activity():
    first = pad(fingerprint="uniq:11")
    second = pad(fingerprint="uniq:22")
    pads = pads_with(first, second)
    identification = pads.identify().until(Button.START)
    pads._queue.append(("uniq:11", EV_KEY, BTN_SOUTH, 1))
    pads._queue.append(("uniq:22", EV_KEY, BTN_START, 1))

    pads.update()

    assert identification.completed
    assert identification.value is second


def test_identification_is_non_blocking_and_value_requires_completion():
    pads = pads_with(pad())

    identification = pads.identify()

    assert identification.listening
    assert not identification.completed
    with pytest.raises(RuntimeError, match="has not completed"):
        _ = identification.value


def test_a_new_identification_interrupts_the_old_request():
    pads = pads_with(pad())
    old = pads.identify()

    new = pads.identify()

    assert old.interrupted
    assert not old.listening
    assert new.listening


def test_identification_can_only_be_narrowed_once():
    identification = pads_with(pad()).identify().until(Button.START)

    with pytest.raises(RuntimeError, match="already has a condition"):
        identification.until(Button.ACCEPT)


def test_identification_rejects_non_button_conditions():
    identification = pads_with(pad()).identify()

    with pytest.raises(TypeError, match="Button predicate"):
        identification.until(1)


def test_first_deliberate_activity_assigns_stable_ids_in_event_order():
    first = pad(fingerprint="uniq:11")
    second = pad(fingerprint="uniq:22")
    pads = pads_with(first, second)
    pads._queue.append(("uniq:22", EV_KEY, BTN_SOUTH, 1))
    pads.update()
    pads._queue.append(("uniq:11", EV_KEY, BTN_SOUTH, 1))
    pads.update()

    assert second.id == 1
    assert first.id == 2
    assert pads[1] is second
    assert pads[2] is first


def test_stick_noise_does_not_identify_but_deliberate_movement_does():
    p = pad()
    pads = pads_with(p)
    pads._queue.append((FP, EV_ABS, ABS_X, 20_000))
    pads.update()
    assert p.id is None

    pads._queue.append((FP, EV_ABS, ABS_X, 22_000))
    pads.update()
    assert p.id == 1


def test_dpad_movement_identifies_a_pad():
    p = pad()
    pads = pads_with(p)
    pads._queue.append((FP, EV_ABS, ABS_HAT0X, 1))

    pads.update()

    assert p.id == 1


def test_same_fingerprint_revives_the_permanent_pad_proxy():
    original = pad()
    pads = pads_with(original)
    pads._queue.append((FP, EV_KEY, BTN_SOUTH, 1))
    pads.update()
    pads._relist({})

    pads._relist({FP: Device("/dev/input/event9", "pretend pad", None, XboxMapper)})
    revived = pads.get(1)

    assert revived is not None
    assert revived is original
    assert original.id == 1
    assert revived.id == 1
    assert pads[1] is revived


def test_disconnected_id_is_reserved_for_its_fingerprint():
    first = pad(fingerprint="uniq:11")
    pads = pads_with(first)
    pads._queue.append(("uniq:11", EV_KEY, BTN_SOUTH, 1))
    pads.update()
    pads._relist({})
    assert pads.get(1) is None

    newcomer = pad(fingerprint="uniq:22")
    pads._pads = [newcomer]
    pads._by_fingerprint = {newcomer.fingerprint: newcomer}
    pads._queue.append(("uniq:22", EV_KEY, BTN_SOUTH, 1))
    pads.update()

    assert newcomer.id == 2
    assert pads.get(1) is None
    assert pads[2] is newcomer


def test_pad_ids_are_unbounded_by_the_four_ports():
    pads = [pad(fingerprint=f"uniq:{number}") for number in range(1, 6)]
    collection = pads_with(*pads)
    for candidate in pads:
        collection._queue.append(
            (candidate.fingerprint, EV_KEY, BTN_SOUTH, 1)
        )
    collection.update()

    assert [candidate.id for candidate in pads] == [1, 2, 3, 4, 5]


def test_gamepad_is_the_lowest_id_connected_pad():
    first = pad(fingerprint="uniq:11")
    second = pad(fingerprint="uniq:22")
    pads = pads_with(first, second)
    pads._queue.append(("uniq:11", EV_KEY, BTN_SOUTH, 1))
    pads._queue.append(("uniq:22", EV_KEY, BTN_SOUTH, 1))
    pads.update()
    assert pads.gamepad is first

    pads._pads = [second]
    pads._by_fingerprint = {second.fingerprint: second}
    assert pads.gamepad is second


def test_gamepad_ignores_connected_pads_without_an_id():
    unidentified = pad()
    pads = pads_with(unidentified)

    assert pads
    assert pads.gamepad is None


def test_get_is_the_forgiving_form_of_stable_id_lookup():
    p = pad()
    pads = pads_with(p)
    pads._queue.append((FP, EV_KEY, BTN_SOUTH, 1))
    pads.update()
    assert pads.get(1) is p
    assert pads.get(9) is None
    with pytest.raises(IndexError):
        pads[9]


def test_empty_pads():
    pads = Pads()
    assert list(pads) == []
    assert len(pads) == 0
    assert not pads
    assert pads.get(1) is None


def test_non_linux_evdev_stays_empty_without_touching_host_proc(monkeypatch):
    """Wine maps host /proc and /dev through Z:, but they are not Win32 pads."""
    monkeypatch.setattr(evdev_module, "_IS_LINUX", False)
    transport = Evdev(deque())
    monkeypatch.setattr(
        transport,
        "_scan",
        lambda: pytest.fail("non-Linux Pads must not scan evdev"),
    )

    assert transport.open() == {}

    assert not transport.running
    assert transport._reader is None


def test_open_builds_pads_from_the_transport_device_list(monkeypatch):
    pads = Pads()
    device = Device(
        "/dev/input/event9",
        "Wireless Controller",
        "DualShock 4",
        SonyMapper,
        {ABS_X: _AxisRange(0, 255)},
    )
    monkeypatch.setattr(pads._transport, "open", lambda: {FP: device})

    pads.open()

    assert len(pads) == 1
    connected = next(iter(pads))
    assert connected.name == "DualShock 4"
    tick(connected, (EV_ABS, ABS_X, 255))
    assert connected.axis(ABS_X) == 1.0


def test_close_disconnects_pads_and_interrupts_identification():
    p = pad()
    pads = pads_with(p)
    identification = pads.identify()

    pads.close()

    assert not p.connected
    assert not pads
    assert identification.interrupted


def test_a_surviving_pad_keeps_its_object_and_its_state_across_a_rescan():
    """A rescan mid-hold must be invisible: same object, same held button."""
    p = pad()
    pads = pads_with(p)
    pads._queue.append((FP, EV_KEY, BTN_SOUTH, 1))
    pads.update()

    pads._relist({FP: Device("/dev/input/event9", "pretend pad", None, XboxMapper)})
    assert pads[1] is p  # same object...
    assert p.held(Button.FACE_SOUTH)  # ...so the hold survived


def test_refresh_ticks_must_be_positive():
    with pytest.raises(ValueError):
        Pads(refresh_ticks=0)
