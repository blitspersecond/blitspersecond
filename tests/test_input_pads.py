"""Discovery, hot-plug, identity and the two modes a game reaches a pad by.

Discovery is faked so the tests are deterministic; what is exercised is the
reconciliation, not the kernel.
"""

import pytest

from blitspersecond.common import fps
from blitspersecond.input.pads import Button, Direction, XboxMapper
from blitspersecond.input.pads.evdev import (
    ABS_HAT0X,
    ABS_X,
    BTN_SOUTH,
    EV_ABS,
    EV_KEY,
    AxisRange,
    PadDevice,
    Report,
    Snapshot,
)
from blitspersecond.input.pads import pads as module

RANGES = {ABS_X: AxisRange(-32768, 32767), ABS_HAT0X: AxisRange(-1, 1)}


class Reader:
    def __init__(self):
        from blitspersecond.input.pads.evdev import layout_for

        self.ranges = RANGES
        self.layout = layout_for(RANGES)
        self.queued = []
        self.closed = False
        self.snapshots = 0
        self.drops = 0

    def arrive(self, *items):
        self.queued.extend(items)

    def read(self):
        items, self.queued = tuple(self.queued), []
        # A real reader counts a drop as it emits the snapshot that replaces
        # what was lost, so this does too.
        self.drops += sum(isinstance(item, Snapshot) for item in items)
        return items

    def snapshot(self):
        self.snapshots += 1
        return Snapshot(frozenset(), {})

    def close(self):
        self.closed = True


def device(fingerprint, vendor="045e", product="02d1"):
    return PadDevice(
        fingerprint=fingerprint,
        path=f"/dev/input/{fingerprint}",
        name="a pad",
        vendor=vendor,
        product=product,
        model="Xbox One Controller",
        mapper=XboxMapper,
    )


@pytest.fixture
def world(monkeypatch):
    """A machine whose pads a test decides."""

    class World:
        def __init__(self):
            self.present = {}
            self.readers = {}
            self.refuse = set()

        def plug(self, fingerprint):
            self.present[fingerprint] = device(fingerprint)
            return self

        def unplug(self, fingerprint):
            self.present.pop(fingerprint, None)
            return self

        def reader(self, fingerprint):
            return self.readers[fingerprint]

    state = World()

    def fake_discover():
        return dict(state.present)

    def fake_open(dev):
        if dev.fingerprint in state.refuse:
            raise OSError("permission denied")
        reader = Reader()
        state.readers[dev.fingerprint] = reader
        return reader

    class Transport:
        discover = staticmethod(fake_discover)
        open_pad = staticmethod(fake_open)

        def close(self):
            pass

    monkeypatch.setattr(
        module, "_transport_for_platform", lambda _logger: Transport()
    )
    return state


def collection(refresh_frames=1):
    return module.Pads(fps.SIXTY, refresh_frames=refresh_frames)


def press(reader):
    reader.arrive(Report(((EV_KEY, BTN_SOUTH, 1),)))


# -- discovery ------------------------------------------------------------


def test_pads_appear_and_are_cut_from_the_frame_they_arrive_on(world):
    world.plug("bt:aa")
    pads = collection()
    pads.cut(0)
    assert len(pads) == 1
    assert len(next(iter(pads)).stream) == 1


def test_there_is_no_cap_on_how_many_pads_may_be_plugged_in(world):
    # Five on a desk while four sit on a sofa. The four-seat limit belongs to
    # Ports; capping here would let a pad that was merely powered on take a
    # place from one somebody picked up.
    for n in range(6):
        world.plug(f"bt:{n}")
    pads = collection()
    pads.cut(0)
    assert len(pads) == 6


def test_a_pad_that_fails_to_open_is_skipped_and_tried_again(world):
    world.plug("bt:aa")
    world.refuse.add("bt:aa")
    pads = collection()
    pads.cut(0)
    assert len(pads) == 0

    world.refuse.clear()
    pads.cut(1)
    assert len(pads) == 1


# -- going away, and coming back ------------------------------------------


def test_a_vanished_pad_goes_inert_rather_than_disappearing(world):
    world.plug("bt:aa")
    pads = collection()
    pads.cut(0)
    pad = next(iter(pads))

    world.unplug("bt:aa")
    pads.cut(1)

    assert pad.connected is False
    assert len(pads) == 0
    assert pad in pads.known        # anything holding it still holds it


def test_a_disconnect_releases_what_the_pad_was_holding(world):
    # A dead battery mid-hold must read as a release, never a stuck button.
    world.plug("bt:aa")
    pads = collection()
    pads.cut(0)
    pad = next(iter(pads))
    press(world.reader("bt:aa"))
    pads.cut(1)
    assert pad.stream.latest.scan.held == frozenset({Button.FACE_SOUTH})

    world.unplug("bt:aa")
    pads.cut(2)
    scan = pad.stream.latest.scan
    assert scan.released == frozenset({Button.FACE_SOUTH})
    assert scan.held == frozenset()
    assert scan.disconnected is True


def test_the_device_node_is_closed_when_the_pad_goes(world):
    world.plug("bt:aa")
    pads = collection()
    pads.cut(0)
    reader = world.reader("bt:aa")

    world.unplug("bt:aa")
    pads.cut(1)
    assert reader.closed is True


def test_the_same_fingerprint_revives_the_same_pad(world):
    # Wireless pads power themselves off after about fifteen minutes. A game
    # holding `pad` must still hold the player's pad afterwards.
    world.plug("bt:aa")
    pads = collection()
    pads.cut(0)
    pad = next(iter(pads))
    press(world.reader("bt:aa"))
    pads.cut(1)
    assert pad.id == 1

    world.unplug("bt:aa")
    pads.cut(2)
    world.plug("bt:aa")
    pads.cut(3)

    assert next(iter(pads)) is pad
    assert pad.connected is True
    assert pad.id == 1              # and its number is still its number


def test_a_different_pad_is_a_different_pad(world):
    world.plug("bt:aa")
    pads = collection()
    pads.cut(0)
    first = next(iter(pads))

    world.unplug("bt:aa").plug("bt:bb")
    pads.cut(1)
    assert next(iter(pads)) is not first


def test_a_departed_pads_ring_stays_contiguous(world):
    # "The last thirty frames" has to mean thirty frames, not thirty frames of
    # presence -- or a recogniser reading across a dropout splices two
    # moments together.
    world.plug("bt:aa")
    pads = collection()
    pads.cut(0)
    pad = next(iter(pads))
    world.unplug("bt:aa")
    for frame in range(1, 6):
        pads.cut(frame)
    assert [w.frame for w in pad.stream.all] == [0, 1, 2, 3, 4, 5]


# -- identity: the wiggle-a-pad mode --------------------------------------


def test_discovery_alone_does_not_earn_a_number(world):
    # A pad merely powered on must not take the number belonging to one
    # somebody picked up.
    world.plug("bt:aa")
    pads = collection()
    pads.cut(0)
    assert next(iter(pads)).id is None
    assert pads.gamepad is None


def test_a_press_is_deliberate(world):
    world.plug("bt:aa")
    pads = collection()
    pads.cut(0)
    press(world.reader("bt:aa"))
    pads.cut(1)
    assert next(iter(pads)).id == 1
    assert pads.gamepad is next(iter(pads))


def test_a_dpad_leaving_neutral_is_deliberate(world):
    world.plug("bt:aa")
    pads = collection()
    pads.cut(0)
    world.reader("bt:aa").arrive(Report(((EV_ABS, ABS_HAT0X, 1),)))
    pads.cut(1)
    assert next(iter(pads)).id == 1


def test_a_drifting_stick_is_not_somebody_using_the_pad(world):
    # The Xbox pad on this desk rests around 1050 of 32767 and wanders.
    # Identifying the wrong pad because one twitched is worse than making a
    # player push a bit harder.
    world.plug("bt:aa")
    pads = collection()
    pads.cut(0)
    world.reader("bt:aa").arrive(Report(((EV_ABS, ABS_X, 1050),)))
    pads.cut(1)
    assert next(iter(pads)).id is None


def test_a_shoved_stick_is(world):
    world.plug("bt:aa")
    pads = collection()
    pads.cut(0)
    world.reader("bt:aa").arrive(Report(((EV_ABS, ABS_X, -30000),)))
    pads.cut(1)
    assert next(iter(pads)).id == 1


def test_the_first_pad_used_keeps_the_glyphs_when_a_second_joins(world):
    # The whole point of a stable number: a title screen showing "Press A"
    # must not swap to another pad's letters because somebody else woke up.
    world.plug("bt:aa").plug("bt:bb")
    pads = collection()
    pads.cut(0)
    press(world.reader("bt:bb"))
    pads.cut(1)
    chosen = pads.gamepad
    assert chosen.fingerprint == "bt:bb"

    press(world.reader("bt:aa"))
    pads.cut(2)
    assert pads.gamepad is chosen


def test_numbers_are_not_reused_by_a_pad_that_never_had_one(world):
    world.plug("bt:aa").plug("bt:bb")
    pads = collection()
    pads.cut(0)
    press(world.reader("bt:bb"))
    pads.cut(1)
    press(world.reader("bt:aa"))
    pads.cut(2)
    assert {p.fingerprint: p.id for p in pads} == {"bt:bb": 1, "bt:aa": 2}


def test_a_pad_is_addressable_by_its_number(world):
    world.plug("bt:aa")
    pads = collection()
    pads.cut(0)
    press(world.reader("bt:aa"))
    pads.cut(1)
    assert pads[1].fingerprint == "bt:aa"
    with pytest.raises(IndexError):
        pads[2]


# -- the boundary ---------------------------------------------------------


def test_a_resume_asks_the_pads_rather_than_assuming(world):
    # Unlike the keyboard, a pad can be asked. A player who held a direction
    # through the pause still holds it.
    world.plug("bt:aa")
    pads = collection()
    pads.cut(0)
    pads.flush()
    pads.cut(1)
    assert world.reader("bt:aa").snapshots == 1
    assert pads.known[0].stream.latest.scan.resynced is True


def test_closing_releases_everything_but_forgets_nothing(world):
    # Ports holds departed pads, so they must survive a teardown of the
    # devices under them.
    world.plug("bt:aa")
    pads = collection()
    pads.cut(0)
    pads.close()
    assert len(pads) == 0
    assert len(pads.known) == 1
    pads.close()        # twice is safe


def test_discovery_does_not_run_every_frame(world):
    # Once a second at the authored rate. Scanning procfs sixty times a second
    # would be the most expensive thing input does.
    pads = collection(refresh_frames=60)
    pads.cut(0)
    world.plug("bt:aa")
    for frame in range(1, 60):
        pads.cut(frame)
    assert len(pads) == 0
    pads.cut(60)
    assert len(pads) == 1


def test_kernel_drops_are_counted_across_every_pad_for_the_frame(world):
    # `Pads.desyncs` is what the loop publishes. It is THIS frame's across
    # every pad: a reconnect brings a fresh reader with a fresh tally, so a
    # running sum over readers would FALL when somebody's battery came back.
    world.plug("bt:aa")
    world.plug("bt:bb")
    pads = collection()
    pads.cut(0)
    assert pads.desyncs == 0

    first, second = sorted(pads, key=lambda pad: pad.fingerprint)
    first._reader.arrive(Snapshot(frozenset(), {}))
    second._reader.arrive(Snapshot(frozenset(), {}), Snapshot(frozenset(), {}))
    pads.cut(1)

    assert first.desyncs == 1
    assert second.desyncs == 2
    assert pads.desyncs == 3

    # This frame's: a quiet frame reports nothing, so a clean run stays silent.
    pads.cut(2)
    assert pads.desyncs == 0


def test_a_departed_pad_cannot_lose_input_it_was_never_sent(world):
    world.plug("bt:aa")
    pads = collection()
    pads.cut(0)
    world.unplug("bt:aa")
    pads.refresh()
    pads.cut(1)

    assert pads.desyncs == 0
