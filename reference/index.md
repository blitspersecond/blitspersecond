# The BPS reference

The engine's internals book: how each part works, why it works that way, why
it does not work some other way, and the measured data where there is any.
For getting started rather than digging in, `bps/guide/` is the other book.

**A list, and not yet a structure.** The set is incomplete, so the grouping
below says what exists, not what the finished shape is.

## [Conventions](conventions.md)

The rules that hold across every subsystem and belong to no pillar: argument
order and the two exceptions to it, the value types a call hands back, the
`(x, y)` / `[row, col]` seam, where the engine's vocabulary stops and a file
format's begins, and the spelling rule.

## [Graphics](graphics/graphics.md)

Everything that reaches the screen, and the one bitmap that answers whether
things touched. The overview page carries the architectural shape — the three
pillars, what they are made of, and where they genuinely differ.

| | |
|---|---|
| [layers.md](graphics/layers.md) | the draw order and the compositor |
| [collision.md](graphics/collision.md) | one bit per pixel: the packed plane and the query |
| [console.md](graphics/console.md) | the glyph grid |
| [tiles.md](graphics/tiles.md) | a world of cells over one sheet, with a camera |
| [sprites.md](graphics/sprites.md) | independently moving things, and their many drawings |
| [quads.md](graphics/quads.md) | ordered textured screen-space quadrilaterals |
| [palette.md](graphics/palette.md) | colour as an index: the bank and the register |
| [atlas.md](graphics/atlas.md) | one image cut into rects |
| [tmx.md](graphics/tmx.md) | reading a Tiled map |
| [sprite-package.md](graphics/sprite-package.md) | reading a `.sprite.json` |

## [The engine](engine/engine.md)

The machine a game runs on: what a frame is worth, the order one is built in,
when it lands on the panel, and where. The overview page carries the shape —
three leaves and two integrators, the two grids that never convert into each
other, and what the `BlitsPerSecond` facade forwards.

**(Alpha)** marks a chapter whose subject has not had a full design pass. What
it describes is what the engine really does, but the design behind it is not
settled. Both Alpha chapters are integrators, and the overview says why that is
not a coincidence.

| | |
|---|---|
| [clock.md](engine/clock.md) | what a frame IS: the fixed step, `time = frame · step`, and who owns 60 |
| [pacing.md](engine/pacing.md) | WHEN a frame lands: one question per presentation slot, the reserve, and how long the pacer makes a second last |
| [loop.md](engine/loop.md) | **(Alpha)** the clearing house: every subsystem serviced in one controlled order, and the frame as a transaction |
| [lifecycle.md](engine/lifecycle.md) | **(Alpha)** CREATED through STOPPED, and the backgrounding axis that is deliberately not a state |
| [display.md](engine/display.md) | WHERE the picture lands, and where the events come from: the boundary with the window system, both ways |

## [System](system/system.md)

Configuration, logging and observability. **The hot path is
the distinction** — the engine is what runs to build a frame, and this is
everything else. **Not written**; the folder exists and `metrics.md` is a
stub.

## [Input](input/input.md)

What the player did, settled onto the engine's frame boundary. The overview
page carries the shape — the cut every device settles onto, one flush with
three answers, and where the devices genuinely differ, including which
transports can report a fidelity the others cannot.

| | |
|---|---|
| [stream.md](input/stream.md) | the retention ring: a window per frame, and how deep |
| [request.md](input/request.md) | a question put to the player, answered without blocking |
| [keyboard.md](input/keyboard.md) | window callbacks in, settled windows out |
| [pointer.md](input/pointer.md) | window pixels to screen pixels, and the ideal mouse |
| [text.md](input/text.md) | a line of typing, and why it is not in the ring |
| [pads.md](input/pads.md) | the closed vocabulary, a pad's window, and which pad is whose |
| [evdev.md](input/evdev.md) | the Linux transport: find, name, calibrate, read |
| [ports.md](input/ports.md) | four durable routes for four people on a sofa — **deferred**, and the page says why |

## [Audio](audio/audio.md)

Making a sound, from a wave in an array to a block the sound card has taken.
The overview page carries the shape — the 400-sample block, the pulled graph,
control as timestamped register writes, and the one thread in the engine.

**(Alpha)** marks the subject, not one chapter. The machinery is settled; the
layer a game reaches for does not exist, so a game builds voices and schedules
writes at sample offsets. The overview says exactly what is missing.

| | |
|---|---|
| [graph.md](audio/graph.md) | stages, lanes and the pull: the four forms, the settled cache, and how a silent voice costs nothing |
| [control.md](audio/control.md) | registers and the timeline: what a write is, when it lands, and driving a register from audio |
| [blocks.md](audio/blocks.md) | one slab, stable row IDs, the delay ring, and why nothing is allocated per block |
| [operators.md](audio/operators.md) | the 24 operators, what each costs, and arity declared by inheritance |
| [sample.md](audio/sample.md) | a recording and playing one: the accepted WAV profile, and `PCM` |
| [instruments.md](audio/instruments.md) | the presets: three roots, eight names, and the level they are not levelled against |
| [transport.md](audio/transport.md) | the device thread and the offline renderer: the blocking write as a clock |

## Not started

**Capture methodology is not a chapter.** Taking and reading a frame-pacing
capture is a runbook, useless away from a terminal, so it lives in the tools:
`util/capture_frame_pacing.py` takes one, `util/frame_pacing.py` reads one, and
`blitspersecond/system/frame_pacing/recorder.py` owns the format. The chapter
says what the numbers have to be; the tools say how to get them.

---

Every chapter states what its subject does today, with the figures measured
rather than estimated, and says plainly where something is unresolved. Where a
page reads thin, the subject is thin — **(Alpha)** marks that explicitly.
