# The engine

The machine a game runs on: what a frame is worth, the order one is built in,
when it lands on the panel, and where.

    bps = BlitsPerSecond()      the engine exists, and is already running
    bps.loop = Title            one callable, sixty times a second
    bps.start()                 the machine begins servicing it

## The shape

**Three leaves and two integrators, and every one of them is about the same
frame.** The leaves each answer one question and are finished. The integrators
exist to coordinate the leaves, which is why they are the two chapters still
moving.

    ┌────────────────────────────────────────────────────────────┐
    │  BlitsPerSecond        the front door: bps.loop, bps.start  │
    └────────────────────────────────────────────────────────────┘
                                     │  one callable
    ┌─────────────────────┐   ┌──────┴────────────────────────┐
    │  Lifecycle          │◄─►│  Loop      one order,          │
    │  WHETHER it turns   │   │            written down        │
    └─────────────────────┘   └──┬──────────┬──────────┬───────┘
                                 │          │          │
                           ┌─────┴───┐ ┌────┴────┐ ┌───┴─────┐
                           │  Clock  │ │ Pacing  │ │ Display │
                           │  WHAT   │ │  WHEN   │ │  WHERE  │
                           └─────────┘ └─────────┘ └─────────┘

**Two grids, and nothing converts between them.** The simulation grid is
`time = frame · step` — exact, even, and never measured. The presentation grid
is the panel's slots — uneven, and discovered by measurement. A frame is
authored against the first and shown on the second, and neither is derived from
the other. The familiar 3, 2, 3, 2, 2 at 60fps on a 144Hz panel is what falls
out of asking one question per slot, not a ratio anybody computed.

**The two grids meet once per iteration, in one direction.** The pacer decides
when the engine is called; the engine's frame count never consults what the
panel did. That is the invariant the whole subject exists to keep, and breaking
it is not subtle — a late present becomes a late simulation, and a compositor
charging a whole extra refresh for a narrowly missed vblank runs the game at
75% of target. [loop.md](loop.md) states it as a rule and
[pacing.md](pacing.md) is the mechanism that keeps it.

**Nothing here schedules anything.** There is no queue, no cadence table, no
callback registry and no `dt`. Each iteration computes where it has to be next
and sleeps until then, and each tick advances the frame count by exactly one.

## The chapters

### The leaves

| | |
|---|---|
| [clock.md](clock.md) | what a frame IS: the fixed step, `time = frame · step`, and who owns 60 |
| [pacing.md](pacing.md) | WHEN a frame lands: one question per presentation slot, the reserve, and how long the pacer makes a second last |
| [display.md](display.md) | WHERE the picture lands, and where the events come from: the boundary with the window system, both ways |

### The integrators

| | |
|---|---|
| [loop.md](loop.md) | **(Alpha)** the clearing house: every subsystem serviced in one controlled order, and the frame as a transaction |
| [lifecycle.md](lifecycle.md) | **(Alpha)** CREATED through STOPPED, and the backgrounding axis that is deliberately not a state |

**(Alpha)** marks a chapter whose subject has not had a full design pass. What
it describes is what the engine really does, but the design behind it is not
settled, so expect the chapter to change with the code rather than only with a
correction. Each says so in its own opening and lists what is unresolved.

**Both Alpha chapters are integrators, and that is not a coincidence.** A clock,
a pacer and a compositor each answer one question and can be finished. The loop
and the lifecycle exist to *coordinate* those, so they cannot be finalised
before the things they coordinate are all present and have stopped moving. A
chapter about one bounded thing is written once; a chapter about the seams
between them is written twice.

**The split is per subject, not per module.** Display is the clear case: its
drawing half is a settled leaf, while its window half is the event source that
input and lifecycle both feed from, and those two halves are at different
stages. Deciding which half a chapter is about is part of writing it.

## The front door is a facade, and it forwards rather than wraps

`BlitsPerSecond` is the only name a game constructs, and constructing it is
idempotent — `BlitsPerSecond()` is how you *get* the engine, from anywhere, not
a thing you do once and keep. **The line between the engine and a game is who
builds the thing.** A game never imports `Clock`, `Display` or `Loop`, and
never constructs one; it says `BlitsPerSecond()` and gets an engine already
holding all of them.

What it holds, it hands over whole. `bps.clock` is the `Clock`, not a view of
one, and `bps.layers` is the draw order itself — so there is no facade chapter
to write. Reading `bps.clock.frame` is reading [clock.md](clock.md)'s subject.

| on the engine | is | described in |
|---|---|---|
| `bps.loop`, `bps.start()` | the game's tick, and the machine servicing it | this page |
| `bps.clock`, `bps.framerate` | the simulation clock | [clock.md](clock.md) |
| `bps.state`, `bps.backgrounded`, `suspend()`, `resume()`, `stop()` | the state machine's verbs | [lifecycle.md](lifecycle.md) |
| `bps.events` | the bus every window event is teed onto | [display.md](display.md) |
| `bps.width`, `bps.height` | 640x360, fixed | [display.md](display.md) |
| `bps.layers` | the draw order | [layers.md](../graphics/layers.md) |
| `bps.keyboard` | the keyboard, cut onto the frame boundary | [input](../input/input.md) |
| `bps.metrics`, `bps.logger`, `bps.config` | the sinks, and the one that is not | [system](../system/system.md) |
| `bps.background` | the clear colour. **Temporary**, and its own docstring says what deletes it | — |

**There is no `bps.display`, deliberately.** A game composes through
`bps.layers` and reads input off the bus, so nothing it does needs to name the
screen. Instruments measuring the engine reach `bps._display` on purpose, and
the private spelling is what marks a probe as measuring the engine rather than
playing a game on it.

## `bps.loop` is a callable, whatever you assign to it

The one piece of machinery that is genuinely the facade's own. Whatever it is
handed is reduced to a zero-argument callable **at assignment**, so the
sixty-times-a-second path is a bare call with no type inspection in it. A
plain function, a lambda, a `GameLoop` instance, or a `GameLoop` subclass —
which is constructed there and then — all arrive at the same shape, and `None`
puts the do-nothing default back. Reading the property back gives what the
engine will actually call, which for a `GameLoop` is its bound `run`.

**A class is constructed every time it is assigned, and nothing is cached.**
`self.bps.loop = Game` with no condition around it does not mean "go to Game",
it means "go to Game, sixty times a second" — building a `Game` and running its
`__init__` each time. Caching one instance per class was tried and reverted: it
made that line allocation-free while the program was still switching scene
sixty times a second, which is the actual defect, and in exchange a re-entered
scene came back holding the previous visit's state. A game rebuilding a scene
every tick should feel like one. See
[`gameloop.py`](../../blitspersecond/gameloop.py) for the taught shape.

**The scene is latched before the call**, so rebinding `bps.loop` during a tick
takes effect on the following one. A transition can never replace code halfway
through a simulation step.

## Constructing the engine is what starts it

`BlitsPerSecond()` walks CREATED → STARTING → RUNNING before it returns, and
everything slow belongs in that stretch — opening the display today, and the
audio readiness gate when there is one. So `bps.start()` starts the *game*, not
the engine: what begins there is the machine servicing your loop, and it does
not return.

The reason warm-up sits in the constructor is a pacing one: no presentation
deadline and no tick baseline exists yet, so slow hardware startup cannot read
as a first-frame stall or a catch-up batch. `Loop` establishes those when it
actually starts. [lifecycle.md](lifecycle.md) owns the rest of it, including
what a half-built engine does when warm-up fails.

## Three clock domains, and none of them divides another

| | counts | paced by |
|---|---|---|
| simulation | frames, as an integer | nothing. `time = frame · step`, exact |
| presentation | panel slots | the panel, discovered by measurement |
| audio | samples, as an integer | the output device's backpressure |

**Audio is a design position taken in advance, not something the tree can be
read to confirm** — it is not in the engine at all yet. The intent is that it
does not arrive in the loop: a blocking worker paced by device backpressure
references neither the frame count nor the slot grid, and a tick issues audio
*events* rather than work for the loop to pump. Nothing shares a timeline with
anything else, so there is nothing for a sliding grid to slide against.
`docs/bps/audio.md` holds that position until the subsystem lands;
[clock.md](clock.md) argues why it does not contradict the fixed step.

## Where the engine stops

The engine sequences a frame and lands it. What goes *in* the frame is
[graphics](../graphics/graphics.md), what the player did is
[input](../input/input.md), and what is written down about the run afterwards
is [system](../system/system.md).

**The hot path is that last boundary.** A metric push happens every frame and
that does not make it engine work: stop pushing and the same pixels land, in
the same order, at the same moments. Metrics, logging and the capture recorder
are sinks. Nothing they receive feeds back into building a frame.
