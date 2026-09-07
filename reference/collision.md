# Collision

> **Not written yet — this is the brief, not the chapter.** The sweep is
> done and the shape is settled; writing is blocked on
> *Collision answers about two different frames* (board, `captured`), because
> the chapter cannot state a temporal contract the engine is about to change.
>
> `docs/bps/COLLISION.md` is the standing design record until this page
> absorbs it. Linked from [console.md](console.md) and [layers.md](layers.md),
> which both defer here.

## The spine

A painted pixel becomes a bit, bits become a plane, two planes meet inside a
box, and the answer is a bool. Write leg first, then read leg — the question
cannot be explained before the thing it is asked of.

## Sections

1. **Intro** — the premise: a layer is one texture, one draw call, one
   answerable collision identity. The shared half is the layout, the
   participant and the question; the masks belong to the pillars.
2. **Limits and costs** — 640x360 is 20 uint32 words with no tail; 29KB a
   plane; the measured table; the full refusal table.
3. **Drawn and solid are one predicate** — index 0 is the alpha key and the
   collision key. Named planes are the exception: authored geometry, never
   drawn.
4. **One bit per pixel** — the layout, why DWORD, `pack` against
   `pack_store` as pattern against store, `unpack` as debug only.
5. **Three representations** — `PixelBuffer.mask`, `Atlas.solidity`, the
   screen plane. *Open: whether `Atlas.solidity` sits on this spine or is
   cited from tiles.md.*
6. **Every pillar fills a plane, and no two do it the same way** — the
   scaffolding tiles.md and sprites.md hang off:

   | pillar | | how | cost shape |
   |---|---|---|---|
   | tilemap | retained | stamps with the quad; coarse scroll slides whole words and rows, fine scroll writes nothing | episodic — 3.61ms full repaint, 0.175ms gutter |
   | sprite | rebuilt | cleared and refilled, grouped by pose, one `stamp_many` a group | continuous — 25k members in 3.14ms |
   | console | derived | pulls on read, retains nothing | nothing if nobody asks |

   Retained wants a scroll trick; rebuilt wants a batch. That is why the two
   optimisation paths diverged rather than one being behind the other.
7. **The offset** — store space against screen space, one number carrying
   apron and sub-word drift, and why a fine scroll moves no bits. Only a
   tilemap has anywhere else to be.
8. **Asking** — the handle, the sugar, the prepared query, `.at()`, bool
   only, screen-space semantics, and the gate that raises rather than
   answering `False`.
9. **The kernel** — the funnel shift, the early-outs, the bounds guarding
   and the fuzz. The honest case for compiling is the offset row, not the
   speed.
10. **When a question is answered** — each pillar's timing, factually, citing
    the board card. Not a contract until that card resolves.
11. **What it is not** — no entities, shapes or physics; not a central
    bitplane stack; not a restamp; not a richer answer than a bool.
12. **Still open** — batching the tile stamp; shadow collider layers building
    GL state; no arbitrary unstamping; a console paying the gather per plane.

## Corrections the sweep found

Re-measured 2026-09-07, warm-up off the metric ring, on a miss:

| claim | where | measured |
|---|---|---|
| "~7us a box" | `collision/query.py` | **1.313 µs** — looks like the pre-kernel NumPy figure |
| "whole-screen query 2.3 µs" | `COLLISION.md` | **8.65 µs** |
| "resolution is the larger half, 0.8µs Python against 0.5µs bit work" | `COLLISION.md` | kernel 0.470 is right, resolution is **0.159**; the Python is the offsets (0.222), the gate (0.066), the clip and the call |

Stale in prose, not measurement:

- `collision/__init__.py` **Status** says the pillars do not stamp yet and
  names `TileMap.collidable` as an invariant with no mask behind it. All
  three stamp.
- `COLLISION.md` cites the loop order `game_loop()` → `layers._advance()` →
  `display.compose()` twice. That order and both names are gone; `prepare`
  now runs *before* the game's tick.
