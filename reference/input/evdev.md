# The evdev transport

Linux offers a gamepad as a file. This layer finds those files, works out what
each one is called, asks the kernel what its numbers mean, and reads them once
a frame.

It decides nothing about what a control *means*. Reports come out as ordered
`(type, code, value)` triples and the pad turns them into buttons and axes —
[pads.md](pads.md) is that half. This is a separate page because it is what
somebody reads when a pad does not work, and because a second transport is
coming and none of this will be true of it.

## Limits and costs

### The surface

```text
discover(procfs=None)     -> {fingerprint: PadDevice}   every pad Linux offers
open_pad(device)          -> PadReader                  open it and calibrate

reader.read()             -> tuple of Report | Snapshot, in order
reader.snapshot()         -> Snapshot                   ask what is true now
reader.layout             which axis is which, for this device
reader.ranges             what each axis's numbers mean
reader.drops              times the kernel has dropped events, cumulative
reader.close()            safe to call twice
```

`discover` takes a captured device list instead of reading the live one, which
is how it is tested and how *"my pad is not detected"* becomes reproducible
without the pad.

### Three ways a device is not offered

| | |
|---|---|
| it does not claim `BTN_SOUTH` | it is not a gamepad |
| vendor is `28de` | Steam — see *Steam hides identity* |
| it has neither a `uniq` nor a `phys` | there is nothing to call it |

A node with no `event` handler is also skipped, because there would be nothing
to open.

### What refuses

**Nothing raises.** An unreadable `/proc` is an empty result rather than an
exception; a device that vanishes between the scan and the open is an `OSError`
the collection swallows and retries on the next refresh; asking a device for an
axis it does not have answers `EINVAL` and the axis is simply absent.

Reading costs one `read` syscall per 64 events plus one to hit `EAGAIN`, once
per frame per pad.

## Finding the pads

Discovery parses `/proc/bus/input/devices` rather than walking
`/dev/input/by-id/`, because **Bluetooth pads get no `by-id` symlink at all**
and a by-id scan silently misses every wireless pad.

**A device is a gamepad if its `KEY` capability mask contains `BTN_SOUTH`
(`0x130`).** Selecting on a capability rather than on a name or a handler
admits exactly the right nodes, and it disposes of three traps without
special-casing any of them:

- **A pad publishes sibling nodes that are not pads.** A DualSense publishes
  three — controller, motion sensors, touchpad — and only one claims a south
  face button. The motion node has no `EV_KEY` at all; the touchpad has
  `EV_KEY` and no gamepad buttons, which is why an `EV_KEY`-only test is not
  enough.
- **Never test for a `js` handler.** joydev is the legacy interface and is not
  a reliable "is a gamepad" signal: a Switch Pro Controller has no `js` node at
  all, only its `event` node.
- **Never test the name.** The kernel calls a Hori Fighting Commander
  `"Generic X-Box pad"`.

The `event` node is captured because it is what gets opened. **It is not the
identity** — it moves across a replug.

## Naming a connection

```text
uniq present  ->  id:<uniq>      the device names itself
otherwise     ->  port:<phys>    the port names the connection
neither       ->  not offered
```

**`uniq` first because it is the more specific answer**, and because `phys` is
actively wrong on Bluetooth: there it is the **host adapter**, shared by every
device paired to it. Measured — two Xbox Wireless Controllers on one desk
report the identical `phys`, as does a pair of headphones. Preferring it would
merge two pads into one.

**`phys` second because a pad that cannot name itself is still enumerated
through a socket.** A USB device is enumerated *through* a port, so the path
exists and survives the replug and re-enumeration that move an event node.

### Asking what a device offers, not how it is attached

The rule branches on what is present, never on the bus. That distinction is
load-bearing: `hid-playstation` names a DualShock 4 by its MAC and leaves
`phys` **empty on both links**, so a rule of the form *"not Bluetooth,
therefore use `phys`"* refuses it outright and the pad does not exist.

It also means **a pad's fingerprint can change with its link, and only for
pads that cannot name themselves.** Measured across six pads: a DualShock 4, a
DualSense and a Switch Pro keep one fingerprint on a cable and over Bluetooth;
both Microsoft pads have no `uniq` over USB and become a different pad when
plugged in.

That is the N64 rule, and it lands on precisely the pads that cannot avoid it:
**a pad moved to a different socket is a different pad.** Swapping two cables
swaps two players, and nobody has ever been confused by that.

### Both anchors are right, one transport each

*A gamepad is a thing a person owns and brings, so the controller is the
anchor* — and *on a GameCube or an N64 it is the port that carries identity*.
Both are true, and the transport decides which applies rather than taste.

**Wired has a socket and it is stable**, so the connection carries the
identity. **Bluetooth has no socket** — a pairing is not a port — so the device
carries it, because its MAC is the only thing that exists.

### Steam hides identity

Vendor `0x28de` is refused before identity is even asked, and the refusal is
**not** that its pads identify themselves badly.

A Steam virtual pad is a **proxy standing in front of a real device and
concealing which one**. Every controller routed through it presents as the same
vendor and product, they carry no `uniq` and no `phys`, and they multiply — one
physical pad becomes several virtual ones. The missing identity is not an
omission; there is nothing to report, because the thing reporting is
synthesised.

Refusing the vendor is how each physical pad is seen **once, as itself**,
rather than once directly and again through a proxy. **A pad counted twice is
worse than a pad counted never**: the duplicate answers to input, so it looks
like it works, and somebody ends up holding one controller that is two seats.

Refusing on what a device *is* beats refusing on what it failed to say.

### A repeated fingerprint keeps the first

Two devices cannot share a Bluetooth MAC and cannot share a USB port path, so
this should not happen. If it does, the first is kept rather than the last —
two pads sharing state would be worse than one of them being missing, and
missing is the symptom somebody notices.

**Identity is needed per session**, long enough to survive a flat battery and a
replug during a run. Nothing here is written down anywhere.

## Asking what the numbers mean

One `EVIOCGABS` per axis, at open, for ten codes: two sticks, two triggers, the
d-pad, and `ABS_GAS`/`ABS_BRAKE`.

**An axis the device does not have answers `EINVAL` or `ENOTTY` and is absent
from the result**, which is how the set of axes a pad *has* is discovered
rather than by asking separately.

**Ranges are not universal, and not even uniform within one pad.** A wired Xbox
One pad reports sticks over −32768..32767, triggers over 0..1023 and the hat
over −1..1, all at once. Assume one range and a fully pressed trigger reads as
three percent.

**A declared but empty range is not an axis.** Real pads do this for controls
they do not have: both Bluetooth Xbox pads declare `ABS_RX`/`ABS_RY` with
`minimum == maximum`, and a Switch Pro declares `ABS_Z`/`ABS_RZ` that way
because its triggers are digital. Such an axis is left out rather than carried
as a zero-width one.

**`fuzz` and `flat` are read and discarded, deliberately.** `flat` is the
kernel's declared deadzone and it is not the one a game wants: a pad on the
test desk declares `flat=128` and rests at 1054, eight times that, so a
flat-sized deadzone lets its own drift through. **The kernel is describing
noise; a deadzone is a judgement about intent**, it is a much larger number,
and it belongs with whatever reads the pad rather than with the transport.

### Normalising

`centred` maps an axis to −1..1 for sticks and hats; `travel` maps it to 0..1
for triggers. A trigger is not a stick with a centre — it rests at its minimum
and has one direction — so reading one through `centred` reports a released
trigger as fully negative.

Two details in `centred` that look like fussiness and are not:

- **The midpoint is integer.** `(min + max + 1) // 2` gives 0 for
  −32768..32767 and 128 for 0..255, which are the two common neutral values. A
  floating midpoint puts both slightly off centre forever.
- **The two halves scale separately**, because a signed range has one more step
  below the midpoint than above it. Scaling by one extent leaves a stick pushed
  fully one way short of 1.

Values beyond the declared range are clamped.

## Which axis is which follows the DRIVER

**The same physical controller reports a different axis layout depending on
which kernel driver claims it.** Not the device, and not the link.

| | wired Xbox (`xpad`) | Bluetooth Xbox (generic HID) |
|---|---|---|
| left stick | `ABS_X` / `ABS_Y` | `ABS_X` / `ABS_Y` |
| right stick | `ABS_RX` / `ABS_RY` | **`ABS_Z` / `ABS_RZ`** |
| triggers | `ABS_Z` / `ABS_RZ` | **`ABS_GAS` / `ABS_BRAKE`** |

**Getting it wrong is not subtle.** Read a Bluetooth Xbox pad through the
`xpad` layout and its right stick is dead while both triggers report
permanently held — `Z`/`RZ` are sticks resting near the middle of a 0..65535
range, which is 0.499 travel against a 0.118 threshold, stable across repeated
readings. Confirmed identically on two Bluetooth Xbox pads.

**So it is decided by capability, not identity:**

> If a device declares `ABS_GAS` and `ABS_BRAKE` with a real range, those are
> the triggers and `Z`/`RZ` are the right stick. Otherwise `Z`/`RZ` are the
> triggers and `RX`/`RY` are the right stick. A device declaring neither
> `Z` nor `RZ` has digital triggers, and `BTN_TL2`/`BTN_TR2` are them.

Measured across six pads, a clean split with no exceptions: wired Xbox, Hori,
DualSense and DualShock 4 on `xpad`; two Bluetooth Xbox pads on generic HID.

Those two constants are queried *because* most pads lack them — their presence
is the whole discriminant, and not querying them is a bug in which the
discriminant can never fire because the fact it needs is never read.

**A pad with no trigger axes maps `BTN_TL2`/`BTN_TR2`, and only then.** A pad
with both sources would have two readings of one control that can disagree near
the threshold, and the axis re-pressing after the button let go would chatter.

**This is what makes a community controller database two thousand entries
long**, and it is the first thing here a device table would genuinely solve. It
still should not be: since the layout follows the *driver*, such a table would
have to encode which driver claims what on which kernel. The declared axis set
simply says.

## Reading at the cut

**There is no thread and no `select`.** The kernel keeps a per-client buffer on
each evdev fd, sized from the device's capabilities and at least 64 events, and
holds everything until it is read. The kernel *is* the staging buffer this
subsystem would otherwise build; a thread copying that buffer into one of ours
a few milliseconds sooner does the same job twice and buys a cross-thread
handoff, a lock, and the engine's only concurrency.

The node is opened `O_RDONLY | O_NONBLOCK` and the drain runs to `EAGAIN`, so
nothing ever waits. A read that blocks is a read on a node somebody chose to
open blocking.

What polling costs is freshness: input landing in a frame's tail waits for the
next cut, the same one-frame granularity the keyboard accepts.

**Never `EVIOCGRAB`.** A grab is exclusive — it would take the pad away from
the browser, the desktop and Steam, so alt-tabbing out of a game would kill the
controller everywhere else. Sharing is how evdev works: every pad node on the
test desk was held open by three processes at once, all reading happily.

### A report is what the kernel called simultaneous

Events are grouped by `SYN_REPORT` into **simultaneity groups**, and the group
is the unit handed upwards. Everything in one happened together; the order of
reports is what makes a roll through down, down-forward, forward
reconstructible rather than flattened into wherever the stick finished.

**An unfinished report is held across the cut.** A group whose `SYN_REPORT` has
not arrived is not finished, and splitting it over two frames would report as
sequential two things the kernel says were simultaneous.

**The timeval is unpacked and dropped.** Within a frame everything is
simultaneous by construction, so a microsecond stamp would be a second game
clock. Order survives the cut; time does not.

Event types the pad cannot use are dropped here rather than carried.

## When the kernel drops

A buffer that overruns between cuts is `SYN_DROPPED`, and it has to be handled
whether or not a thread exists — a thread moves the failure, it does not remove
it.

What is lost is not merely some events. **A release lost in a dropped run
leaves a button held for good**, because the held-set is long-lived. Only a
wholesale replacement heals it.

So on a drop:

1. the partial report it belonged to is discarded rather than emitted with a
   hole in it;
2. everything from the drop onward is discarded too;
3. the device is **asked** what is true, and that answer is emitted in their
   place.

**The snapshot is taken after the drain, not at the drop.** It therefore
already includes whatever the discarded reports would have said — keeping them
would apply stale history on top of current truth, in the wrong order.

`EVIOCGKEY` returns one bit per key for everything currently down and
`EVIOCGABS` carries each axis's current value in its first field. Both are the
kernel's own record of device state, so neither depends on having seen the
events that produced it, which is exactly why this is the answer to a drop. If
the ioctl itself fails, the answer is "nothing held" rather than a guess.

**A snapshot REPLACES rather than adds**, and a reader must treat it that way:
a button absent from it is up, including one the reader believed was held.
`drops` counts how many times this has happened, cumulatively and never reset.

The same mechanism answers a resume, where ticks stopped and the world moved on
unwatched. A pad can be asked; the keyboard cannot.

## The vocabulary is translated here

evdev codes become `Button` members at **this** boundary, not on the mapper and
not downstream.

An evdev code and a Windows bitfield describe the same physical button with
different numbers, so each is translated at its own boundary and nothing
downstream can tell which platform it is reading. Putting the table on the
mapper instead would make one platform's numbering the engine's intermediate,
and a reading from the other would be translated twice to arrive somewhere it
could have reached directly. **A mapper's job is what a pad calls its buttons;
what a transport's numbers mean is the transport's own business.**

The d-pad is deliberately absent from the axis table: `ABS_HAT0X`/`Y` is how
evdev spells a d-pad, and a d-pad is not two axes.

`BTN_THUMBL` and `BTN_THUMBR` are named and never mapped, so the absence is
visibly deliberate rather than an oversight.

## What it is not

**Not the pad.** Nothing here knows what a control means, what a family calls
it, whether a trigger counts as pressed, or which pad a person picked up.

**Not portable, and not pretending to be.** Everything on this page is Linux.
The shape it produces — ordered facts in simultaneity groups, with a way to ask
the device what is true — is what a second transport has to supply, and that is
the whole of what it has to supply.

**Not a permission problem.** Reads work with no setup because logind tags
joystick nodes for the active local seat and grants the user an ACL on the
node. The old "add yourself to the `input` group" advice predates that being
standard. It is still the fallback where a per-session ACL does not reach — over
SSH, a background daemon, a non-active user — and an `EACCES` on open is what
points at it. A shipped game should document that, not require it.

## Still open

**A degenerate range is refused at calibration but also guarded downstream.**
An axis with `minimum == maximum` never becomes an `AxisRange`, so `centred`'s
`extent <= 0` branch is unreachable by the normal path. It answers 0.0, which
is correct, but it is currently a guard rather than a statement about a control
that is not there.
