# Reading a TMX file

Tiled is the editor, TMX the file it writes, and `TiledFile` is the reader.

    maps   = TiledFile("island.tmx")
    maps.names                          # every tile layer, back to front
    island = maps.tilemap("blue_island")
    bps.layers.add(island)

**BPS is not a second Tiled.** It accepts the subset that maps exactly onto
`TileMap` and refuses the rest **by name, at load** — while the file is still
the thing you can fix, and with a message that says which element was wrong
and why the engine cannot take it. Nothing here is a capability the hand-built
road lacks; a world comes from a file OR from a game writing through `cell`,
and both are first-class. See [tiles.md](tiles.md) for the pillar this feeds.

Two roads out of a file, and they answer different questions:

    maps.tilemap(name)      a fresh TileMap, drawable, yours to deform
    maps.layer(name)        the record: a read-only world and its sheet

The second is the data road — take it when the world you want is not literally
what the file said, such as a plane stitched from two files.

## Limits and costs

### The accepted profile

| element | accepted |
|---|---|
| `<map>` | `orientation="orthogonal"`, finite (`infinite="0"`), `renderorder="right-down"`, positive `width`/`height`/`tilewidth`/`tileheight` |
| `<tileset>` | at least one, ordered by `firstgid`, names unique. Inline or through a TSX `source` |
| a tileset's grid | `tilewidth`/`tileheight` must equal the map's, `spacing` and `margin` both zero, exactly one sheet `<image>` |
| a tileset's declaration | `columns` and `tilecount`, where given, must match what the image actually cuts into |
| a sheet's colour | indexed, RGB or RGBA — all draw. Only an indexed sheet can collide |
| `<layer>` | at least one, names unique and non-empty, dimensions equal to the map's, `visible="1"`, `opacity="1"`, `parallaxx`/`parallaxy` both 1 |
| `<data>` | finite and unchunked, `encoding="csv"` or `"base64"`, `compression` absent, `"zlib"` or `"gzip"` |
| a layer's tiles | from **one** tileset. An empty layer is fine and takes the map's first |
| orientation bits | horizontal flip, vertical flip and diagonal transpose. A diagonal turn only where the sheet's cells are square |
| `offsetx`/`offsety` | any integer; becomes the map's `origin`. Absent means `(0, 0)`, which is every ordinary file |

Everything else refuses. `<objectgroup>`, `<imagelayer>` and `<group>` are
named as unsupported layer types; hexagonal rotation is refused as an
orthogonal-only reader; image collections are refused because there is no one
sheet to cut.

### Costs

Ryzen 7 5700X3D, and the image itself is excluded — a `PixelBuffer` is
interned by path and shared with every other consumer of that art.

| | |
|---|---|
| parse a 15-cell file | 654µs |
| parse a 7,168-cell file | 990µs |
| parse a 16,320-cell file | 1.20ms |
| naming a file already read | 2.5µs |
| minting a `TileMap` from it | 40–111µs, with the world's size |

Parsing is load-time work and it is not on any frame path. The fixed part
dominates a small file: XML, the tilesets and their images resolved and cut.

### What refuses

Every message names the file, the element and what was wrong with it. Four
are worth knowing because they refuse something Tiled will happily let you
author:

**More than one tileset in a layer.** One `TileMap` draws from one sheet, and
that is what buys a single texture bind and a single draw call for the whole
layer at any tile count. Split it into one Tiled layer per sheet; they become
one BPS layer each, which is also how you get two collision identities.

**A diagonal turn on a rectangular tile.** A quarter turn on a 16x8 cell makes
it 8x16, which either overflows the neighbouring cells or has to be squeezed —
and squeezing stops the pixels being square. Square tilesets take all eight
orientations; rectangular ones take the four that leave the shape alone. Tiled
lets you author it, so the map is not broken; BPS will not draw it.

**Spacing and margin.** An `Atlas` cuts a sheet into touching cells. Nothing
in BPS ships art that needs gaps, and guessing them would be worse than the
refusal.

**Hidden, faded and parallax layers.** All three are decisions the game makes
at runtime, through `enabled`, the compositor, and scrolling two maps at
different rates. Reading them from the file would bake a view decision into an
asset.

## What comes out

`TiledFile` is a cached, immutable **asset**: naming the same file twice gives
the same reading of it, interned and weakly held on the same terms as
`PixelBuffer`, and re-read once the last holder lets go. It carries the map's
size in cells, its cell size in pixels, and its layers in the artist's
back-to-front order — so iterating `names` and adding each map reproduces the
artist's stacking as the draw order.

A `TiledLayer` is one tile layer's record: its name, its sheet already cut,
its world, and the origin the artist gave it.

**Its world is read-only, deliberately.** The file is shared, so it is the
file's reading of the world, and two maps minted from it must not be able to
edit each other. `tilemap()` copies out of it; so should you.

**The layer and its sheet are paired because the parser PROVED the pairing**,
refusing any layer whose tiles span more than one tileset. Carrying the sheet
on the record is what stops every consumer re-deriving a fact settled at parse
time.

## Minting is not interned, and that is the point

Every `tilemap()` call builds a fresh `TileMap` with its own writable copy of
the world. Asking twice gives two maps that can be dug through independently —
eating the dirt in a Boulder Dash without destroying the file's world — and a
caller who wants one map in two places already has `a = b`.

**Identity belongs to the file, which is cached; the world you mint from it is
yours.** Memoising mints per layer name breaks three ways here. Interning the
file makes it non-deterministic: drop the `TiledFile` while keeping the map,
ask again, and the weak table has let go, so the same call answers
differently. The minted world is writable, so a shared mint is a shared
mutable world rather than merely a shared camera. And a `TileMap` IS a
`Layer`, so handing the same one back twice makes adding it to the draw order
twice easy to write.

A minted map arrives unattached and empty. Unattached is the law every layer
obeys — build it, then place it — and nothing is stamped until the camera
runs, so it fills on its first compose. Its `tag` defaults to the layer's
name, which is what you would otherwise write on the next line.

## From a global id to a cell

TMX packs a cell into one `uint32`: the top bits say how the tile is turned,
the rest is a global id, unique across every tileset the map refers to.

A global id resolves to its owning sheet by `firstgid` — the tilesets are
ordered, so the owner is the last one whose `firstgid` the id reaches, and an
id past that sheet's tile count belongs to no tileset and refuses. The local
index a `TileMap` stores is the id minus its owner's `firstgid`; zero means no
tile, which becomes `EMPTY_TILE`.

**The three orientation bits are read as a mask and a shift rather than a
translation**, because `Orientation` took Tiled's bit layout and its
composition order deliberately. Transpose, horizontal flip and vertical flip
are the same three spells in the same order on both sides of the boundary.
The fourth flag, hexagonal rotation, has no meaning in an orthogonal reader
and refuses.

**The transpose happens once, at load.** TMX stores rows and the engine
addresses worlds `[x, y]`, so the decoded plane is transposed and made
contiguous here — after which every read downstream is native, and nothing
pays per frame or wraps an accessor around it.

The whole decode is vectorised: the flags are three shifted comparisons over
the array, the local indices are one subtraction under a mask, and the
diagonal-turn check is one test over the transposed bits that reports the
first offending cell by coordinate.

## What it is not

**Not a renderer, and not a consumer of GL.** It reads XML, decodes indices
and cuts an `Atlas`, all of which work without a window — so a world can be
loaded before there is a display, and a failure to load happens while the
filename is still in hand rather than at first draw.

**Not the privileged road in.** A world written cell by cell through `cell` is
the same world by the time anything draws it.

**Not PyTMX**, which is the library a reader would otherwise reach for, and
it is not used anywhere in the engine — `tiled.py` is `xml.etree`, `base64`,
`gzip` and `zlib`, all stdlib. Two reasons, and the first is the hard one.

*Licensing.* PyTMX is LGPLv3 and BPS is MIT. Linking it would put relinking
obligations onto every game that embeds this engine, which is not a cost an
engine gets to accept on its users' behalf.

*It invents global ids.* PyTMX bakes the flip flags into **synthetic gids**
for flipped variants, so a raw layer grid can hold ids past the tileset's own
tilecount, and a consumer has to map them back to get an index and its flags.
Reading the bits directly is a mask and a shift, and it stays that way
precisely because `Orientation` took Tiled's bit layout and composition order
deliberately. Undoing someone else's re-encoding of a thing the file already
said plainly is the wrong direction of work.

The whole reader is about 560 lines, most of which is the refusals — the
parsing itself is short because the accepted profile is small.

**Not a place the file format's vocabulary escapes.** `tilewidth`, `firstgid`
and `parallaxx` are TMX's spelling and they stop at the parser; what reaches a
caller is `Size`, `Atlas`, `Orientation` and `PLANE_DTYPE`. The private
tileset record exists only for the load — publishing it would hand a caller
the format's bookkeeping after the parser had already discharged it.
