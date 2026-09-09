# The BPS reference

The engine's internals book: how each part works, why it works that way, why
it does not work some other way, and the measured data where there is any.
For getting started rather than digging in, `bps/guide/` is the other book.

**A list, and not yet a structure.** The set is incomplete, so the grouping
below is provisional — it says what exists, not what the finished shape is.

## The frame

| | |
|---|---|
| [layers.md](layers.md) | the draw order and the compositor: what `bps.layers` owns, the prepare/commit frame, blend, and where a layer's settle happens |
| [collision.md](collision.md) | one bit per pixel: the packed plane, the query surface, the compiled kernel, and how the three pillars differ in filling one |

## The pillars

| | |
|---|---|
| [console.md](console.md) | the glyph grid: a cell as one number, the variant table, the pen, and what recomposes |
| [tiles.md](tiles.md) | a world of cells over one sheet, with a camera: `cell` as the only road in, the retained window, and the coarse/fine split |
| sprites.md | **not written.** The pillar is built; the chapter waits on it settling |

## What the pillars are made of

| | |
|---|---|
| [palette.md](palette.md) | colour as an index: the 256-row bank, the register that picks a row, the modify path, and the pin that keeps drawing and collision agreeing |
| [atlas.md](atlas.md) | one image cut into rects, shared by all three pillars: the autogrid ruling, the rect table, the keying, and what is derived from a rect |
| [tmx.md](tmx.md) | reading a Tiled file: the accepted profile, every refusal by name, and the global-id resolution |

## Not started

Input, frame pacing, and audio. `docs/bps/` holds the standing authority for
each until its chapter exists — `INPUT_STREAM.md`, `FRAME_PACING.md` and
`DECOUPLED_PRESENTATION.md`, `audio.md`.

---

`docs/bps/writing-a-reference-chapter.md` is the process these are written by,
including when a chapter is due and what happens to the source once one ships.
