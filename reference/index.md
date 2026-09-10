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

What the player did, settled onto the engine's frame boundary. **Not written,
and not yet writable** — input is next in line for the full porting process,
so the page is a marker for other chapters to point at rather than a brief.

## Not started

Audio. `docs/bps/audio.md` holds the standing authority until its chapter
exists.

**Capture methodology is not a chapter.** Taking and reading a frame-pacing
capture is a runbook, useless away from a terminal, so it lives in the tools:
`util/capture_frame_pacing.py` takes one, `util/frame_pacing.py` reads one, and
`blitspersecond/system/frame_pacing/recorder.py` owns the format. The chapter
says what the numbers have to be; the tools say how to get them.

---

`docs/bps/writing-a-reference-chapter.md` is the process these are written by,
including when a chapter is due and what happens to the source once one ships.
