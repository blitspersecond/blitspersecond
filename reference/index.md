# The BPS reference

The engine's internals book: how each part works, why it works that way, why
it does not work some other way, and the measured data where there is any.
For getting started rather than digging in, `bps/guide/` is the other book.

**A list, and not yet a structure.** The set is incomplete, so the grouping
below says what exists, not what the finished shape is.

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
when it lands on the panel, and where. **Being written** — the overview and
most chapters are stubs listing what they owe.

| | |
|---|---|
| [pacing.md](engine/pacing.md) | WHEN a frame lands: one question per presentation slot, the reserve, and how long the pacer makes a second last |
| [clock.md](engine/clock.md) | *stub* — what a frame IS |
| [loop.md](engine/loop.md) | *stub* — every subsystem serviced in one controlled order |
| [lifecycle.md](engine/lifecycle.md) | *stub* — CREATED through STOPPED, and the backgrounding axis |
| [display.md](engine/display.md) | *stub* — WHERE the picture lands |
| [metrics.md](engine/metrics.md) | *stub* — the instruments |

## Not started

Input and audio. `docs/bps/` holds the standing authority for each until its
chapter exists — `INPUT_STREAM.md`, `audio.md`.

**Capture methodology is not a chapter.** Taking and reading a frame-pacing
capture is a runbook, useless away from a terminal, so it lives in the tools:
`util/capture_frame_pacing.py` takes one, `util/frame_pacing.py` reads one, and
`blitspersecond/system/frame_pacing/recorder.py` owns the format. The chapter
says what the numbers have to be; the tools say how to get them.

`docs/bps/DECOUPLED_PRESENTATION.md` is no longer the authority on how pacing
works — `engine/pacing.md` is — but it still holds material `engine/clock.md`
owes and has not taken.

---

`docs/bps/writing-a-reference-chapter.md` is the process these are written by,
including when a chapter is due and what happens to the source once one ships.
