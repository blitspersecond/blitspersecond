# The sprite package

A `.sprite.json` package says **how art is structured** — never where the
pixels come from.

    a package  is to a sprite sheet  what  a TMX  is to a tileset

    weiss = SpriteSheet("weiss.sprite.json")     # the package road
    crate = SpriteSheet("crates.png", 16, 16)    # no package at all

So a package is **optional**, and it is not the privileged road: a bare
indexed image read as a grid synthesises the same records, and a game
generating its own sheet never touches the loader. What a package buys is
named poses, multipart composition, palette banks, authored collision planes
and clips.

It is deliberately auditable: JSON, one file, diffable, and compilable from
Aseprite by `util/aseprite_package.py`. **Aseprite is a first-class authoring
input and never the runtime representation.**

**Validation is the point.** Every invariant below becomes an error with names
in it — which tileset, which assembly, which frame — so a bad export fails at
load, while the file is still the thing you can fix, rather than as garbage
pixels three subsystems later. See [sprites.md](sprites.md) for the pillar
this feeds.

## Limits and costs

### The shape

```json
{
  "version": 1,
  "tick_unit": "integer simulation tick",
  "tilesets":   { "<name>": { ... } },
  "assemblies": { "<name>": { ... } },
  "animations": { "<name>": { ... } }
}
```

| key | required | rule |
|---|---|---|
| `version` | **yes** | exactly `1` |
| `tilesets` | **yes** | at least one |
| `assemblies` | **yes** | at least one |
| `animations` | no | absent or `{}` is a sheet with no clips |
| `tick_unit` | no | documentation only; the loader never reads it |

`tick_unit` is inert. Both authored fixtures carry `"integer simulation
tick"`; the bullets package omits it and loads identically. Durations **are**
integer game ticks and the loop's rate is the game's to declare, so the field
is a note to a human about what the art was authored against.

### A tileset

```json
"tilesets": {
  "character": {
    "image": "chun-li.png",
    "primary": true,
    "transparent_index": 0,
    "default_palette": 0,
    "palettes": [ ["#00000000", "#182038ff", ...], ... ],
    "tiles": { "sprite-19472": { "source": [1, 1, 83, 101] } }
  }
}
```

| field | required | rule |
|---|---|---|
| `image` | **yes** | a filename, resolved **relative to the package file** |
| `tiles` | **yes** | at least one; `name -> {"source": [x, y, w, h]}` |
| `palettes` | **yes** | at least one table, at most 256 |
| `default_palette` | no | index into `palettes`, default `0`, must be in range |
| `transparent_index` | no | default `0`, and **must be** `0` |
| `primary` | see below | at most one tileset in the package |

**The image must be indexed (mode `P`).** A sprite tileset is an index texture
read through a palette bank at draw time, so an RGB(A) picture has no indices
to read. `TileMap` is the pillar for those.

Image paths resolve against the **package file**, so a package and its art
travel together. `PixelBuffer` interns by absolute path, so two tilesets
naming one image share the buffer and its texture.

### Source rects

**`tiles` is not a grid, and that is the whole reason the format exists.**
Chun-Li's three tilesets hold 1,741 rectangles in 982 distinct sizes. Every
source rect must be non-negative, positively sized, and lie wholly inside its
image — checked here, then re-checked by `Atlas` when the tileset is cut, so a
rect running off the edge is refused at load rather than returning a silently
short slice at draw time.

**Rects may overlap, abut or leave gaps.** Nothing about a tileset implies a
partition of its image.

### Palettes are authoritative; the PNG's own is a preview

A package's `palettes` are what the artist meant. The image's embedded table
is an authoring convenience and the loader never reads it on this road.

Each entry is a list of at most 256 `"#rrggbbaa"` literals, and **alpha is
all-or-nothing**: `00` at index 0, `ff` everywhere else, nothing between. A
mid-table transparent entry or a translucent colour is refused outright.

BPS has no palette-space alpha by design, and that is a rule about the whole
pipeline rather than about tables: the engine paints back to front, colour
information is last-writer-wins, and a sprite pixel is drawn or discarded. **A
fade is another ROW of the bank** — a dimmer copy of the same table — or
stipple, or `layer.blend` on the whole layer.

A **bank** is several tables over one image, selected per presentation at
runtime with `sprite.palette_index["character"] = 3`. The authored tables land
in rows 0 upward and the rest of the 256 stay as the system palette, spare for
derived shades. Chun-Li carries 28 per tileset: rows 1–6 are costume swaps
differing in 9–16 of 64 entries with the skin ramp shared — the CPS3
colour-select model — and 7–27 are flash and effect tables differing at 37–63.
The engine stores them flat and models none of that.

`default_palette` is the row a fresh register starts on. It is loaded into the
bank's own `active` register and read back off it; there is no second field
holding it. See [palette.md](palette.md).

### `"primary": true` — which art the instanced presentations use

Slot 0 may draw from any tileset the package has, and a multipart pose spans
several. **Every other presentation is pinned to one tileset**, which makes
the instanced block a single texture under a single bank.

**A package with a choice to make must make it.** One tileset needs no flag,
because there is nothing to choose — so every grid sheet and both first-party
fixtures say nothing. More than one and the flag is **required**.

Deliberately not *the first one wins*: JSON object order is not a contract. A
prettifier, a `sort_keys=True`, or any tool that round-trips the file may
reorder it, and a silently different primary tileset surfaces as art that
stopped drawing rather than as an error. Kris: *"first is a bad rule, the json
may get resorted."*

It sits **on the tileset**, not at the top of the file, because the fact
belongs to the thing it describes: there is no name to misspell and no pointer
to update when a tileset is renamed. The cost is that a flag on each object
cannot express "exactly one" structurally, so the loader enforces it. Not
`"default"`, which would sit beside `default_palette` in the same object
meaning something entirely different. `"primary": false` is not a claim, so an
exporter may write the key on every tileset.

**It is authored rather than inferred**, because the tempting guess — whatever
tileset slot 0 is currently using — moves every tick under a clip and spans
two tilesets during a multipart pose. It would invalidate a population
mid-animation. This is a fact about the art, not about a pose.

### An assembly

```json
"assemblies": {
  "sprite-19472-hit-001": {
    "parts": [
      {"tileset": "character", "tile": "sprite-19472",
       "offset": [-48, -96], "order": 0}
    ],
    "collision": {
      "body_hurt": [[-8, -96, 22, 17], [-24, -82, 53, 16]],
      "push":      [[-22, -77, 44, 77]]
    }
  }
}
```

**Timeless is the point.** An assembly owns no clock, no frame counter, no
position and no velocity. It is *what* is shown, never *when* or *where*.

| part field | required | rule |
|---|---|---|
| `tileset` | **yes** | must name a tileset in this package |
| `tile` | **yes** | must name a tile in that tileset |
| `offset` | **yes** | `[x, y]`, signed, in the assembly's local frame |
| `order` | no | default `0`, and must be **non-decreasing** through the list |

`order` being non-decreasing means **the file's order IS the draw order** and
nothing sorts at runtime. A file listing parts out of order is refused rather
than quietly sorted, because sorting it would hide an exporter bug.

`offset` is load-bearing: packed tiles have different dimensions, and the
offset puts each back into the shared local frame without encoding movement.

**`parts` may be empty.** That is a deliberate blank pose — six of Chun-Li's
3,198 assemblies are one — and presenting nothing is a real state.

**There is no per-part `palette`, and one is refused rather than ignored.**
Parts from different tilesets already draw under different registers, because
a register is per tileset; two parts of one tileset wanting different tables
is a sprite of its own, which is a layer of its own. Silently dropping the
field would change how such a package draws, so the loader raises and names
the road.

### Collision planes

`collision` maps an opaque plane **name** to a list of boxes in the same local
frame. Boxes are `[x, y, w, h]` and **signed** — the anchor is `(0, 0)` and
geometry sits around it, so negative origins are the normal case.

The engine reserves no names and knows no semantics. Third Strike authors six
— `attack`, `body_hurt`, `limb_hurt`, `push`, `throw`, `throwable` — because
fighting-game feel wants hurt regions that deliberately differ from the art.

**`collision` is optional, and omitting it is not a lesser package.** Every
sheet has a default plane — the occupied-pixel silhouette, derived from the
parts — and that is the heritage path rather than a fallback: an R-Type boss's
hittable surface *is* its pixels. Named planes are opt-in refinement.

The union of every plane name any assembly declares becomes the sheet's
**vocabulary**, and a collision query's selector is validated against it, so a
typo'd plane name is an error rather than a hitbox that never hits.

### Animations

```json
"animations": {
  "stance": {
    "frames": [
      {"assembly": "stance-01", "duration_ticks": 4},
      {"assembly": "stance-02", "duration_ticks": 4}
    ]
  }
}
```

| field | required | rule |
|---|---|---|
| `frames` | **yes** | at least one |
| `assembly` | **yes** | must name an assembly in this package |
| `duration_ticks` | **yes** | a positive whole number of game ticks |

**Milliseconds never appear.** A clip advances on the fixed-step loop or not
at all, and the milliseconds an authoring tool exports are converted by the
compiler, not carried.

**Clips reference assemblies, never the reverse** — see *an orphan assembly is
legal*, below.

### Costs

| | |
|---|---|
| a package, cold | **0.82ms** for fumiko — 32 poses, 8 clips, a 768x32 image |
| the same, art already interned | 0.63ms |
| a two-pose package, cold | 0.34ms |
| of which, parsing the JSON | 136µs |
| a bare image as a grid | 225µs |
| naming a sheet already loaded | **5.4µs** |

### What refuses

| | |
|---|---|
| a top level that is not a JSON object | `ValueError` |
| any `version` but `1` | `ValueError`, naming what it read |
| no tilesets, no assemblies, or a tileset with no tiles | `ValueError` |
| an animation with no frames | `ValueError` |
| an `image` that is not a filename | `ValueError` |
| an image that is not mode `P` | `ValueError`, naming the mode and pointing at `TileMap` |
| a `transparent_index` other than `0` | `ValueError` |
| no `palettes` | `ValueError` — an indexed image is unreadable without one |
| more than 256 palettes, or a table over 256 entries | `ValueError` |
| a colour that is not `"#rrggbbaa"` | `ValueError`, naming the entry |
| alpha other than `00` at index 0 or `ff` elsewhere | `ValueError`, naming the entry and the law |
| `default_palette` out of range | `ValueError`, giving the count |
| a source rect that is not `[x, y, w, h]`, is non-positive, has a negative origin, or runs outside the image | `ValueError`, naming the tile |
| a part naming an unknown tileset or tile | `ValueError`, listing what the package has |
| a part with no `[x, y]` offset | `ValueError` |
| a part `order` lower than the one before it | `ValueError`, naming both |
| a per-part `palette` | `ValueError`, naming the register that replaces it |
| a clip frame naming an unknown assembly | `ValueError` |
| a `duration_ticks` that is not a positive integer | `ValueError` |
| two tilesets marked `"primary": true` | `ValueError`, naming both |
| several tilesets and none marked | `ValueError`, listing them and saying what to write |

Every one is covered by `bps/tests/test_sprite_sheet.py`, which builds a
minimal valid package and then breaks it one rule at a time.

## Naming is opaque

**The engine reserves no names.** Tileset, tile, assembly, plane and animation
names are keys the package supplies, and nothing downstream parses one. The
conventions in the shipped fixtures are artifacts of the tool that produced
them, and none is a rule:

| convention | where from | example |
|---|---|---|
| `<tag>-<n>` | Aseprite, via `util/aseprite_package.py` | `idle-0`, `attack-3` |
| `frame-<n>` | the same, for a frame carrying no tag | `frame-0` |
| `sprite-<id>` | the Third Strike extractor | `sprite-19472` |

### An orphan assembly is legal

**Aseprite will not let you put a label on a single frame that is not part of
a loop.** Tags cover ranges, so a standalone pose — a hit reaction, a landing
frame, anything a game presents directly rather than plays — cannot be tagged
and comes out of the compiler untagged. That is the reason `frame-<n>` exists,
and the reason **an assembly need not belong to any clip**.

Fumiko is the worked example: 32 assemblies, 31 of them `<tag>-<n>` and
reachable from one of its 8 clips, and one `frame-0` reachable from none. It
is a perfectly ordinary pose, shown with `sprite.assembly = "frame-0"`.

A loader that tidied away an unreferenced assembly would silently delete a
usable pose, so nothing does. The corollary matters to anyone writing a
compiler: **the assembly set and the clip set are not the same set.**

## The grid road

    SpriteSheet("bullets.png", 8, 8, -4, -4)

A bare indexed image read as a uniform grid, synthesising the same records
with no package file:

- one tileset, named `sheet`;
- one single-part assembly per cell, named by its **row-major index** as a
  string — `"0"`, `"1"`, …;
- anchored at the cell's top-left, shifted by `ox`/`oy`;
- no clips, no named planes.

The anchor is the cell's top-left because a bare grid cell is still a quad,
and a quad goes down from its top-left everywhere else in the machine.
`ox`/`oy` are the format's own `offset` with the same meaning and the same
sign, applied to every cell. `(-4, -4)` on 8x8 tiles centres — which is
precisely what the bullets package authors by hand, and what a danmaku bullet
wants: its position **is** its centre, so spawn maths is polar from there and
a small central hitbox sits on it.

**This inverts the palette law in exactly one place.** There is no package, so
the image's embedded palette **is** the table, written into row 0. A file with
no embedded palette leaves row 0 as the system palette, which draws in real
colours rather than in nothing. Indexed is still required.

A grid sheet is additionally addressable by index or `(col, row)`, the way an
autogrid `Atlas` is. A package sheet is not: its assemblies have authored
names, so asking one for cell 3 is refused rather than answered by
coincidence.

## What a package deliberately does not carry

- **Game data.** Provenance, native command programs, move names, movement
  tables, damage, effect dispatch: all sidecar. Another game must be able to
  consume a package without knowing what a hurtbox means.
- **Milliseconds**, anywhere authoritative.
- **Continuous rotation or scale.** Quarter turns are per presentation and
  live in the runtime. Baked angle steps are just more assemblies, which is
  the model working rather than a gap.
- **Playback state.** No current frame, no position, no velocity.

## Fixtures

| | where | shape |
|---|---|---|
| `fumiko` | `bps/assets/fumiko/` | 1 tileset, 32 one-part poses, 4 palettes, 8 clips, no planes. CC0 |
| `bullets` | `bps/assets/bullets/` | 1 tileset, 2 poses, 1 palette, no clips, `[-4, -4]` baked. First-party |
| `chun-li` | the `testsuite` repo | 3 tilesets, 3,198 poses, 28 palettes each, 74 clips, 6 planes, 982 distinct rect sizes. **Capcom's** — never in the engine repo |

The first two are first-party and in the engine repo on purpose: between them
they cover both shapes the pillar serves — a few rich presentations, and a lot
of uniform cheap ones — so the tests prove the loader from a clean checkout.
