# Palette

A colour in BPS is an **index**, not a triple. Art stores the number 5; what 5
looks like is a palette's business. That one decision is why recolouring is
never a pixel job, why a mirror match costs one copy of the art, and why a
river can move without a single pixel changing.

This chapter follows one index from the art to a colour on screen: the table
that resolves it, the bank that holds 256 tables, the register that picks one,
the upload, and the shader that fetches it. `ColorQuad` is here too — it is
four bytes and a name, and it belongs where people look for it.

The **guide** introduces palettes; this is what they are and what they cost.

## The table

A palette is 256 entries of RGBA, always. Not "up to" — a package authoring
eight colours still gets 256, and the rest hold the default.

That default is the **system palette**: index 0 the transparent hole, 1..16
the HTML 4.01 / VGA set every machine since CGA has agreed on, and 17..255
black and opaque. There is no function to build it and nothing to install; a
bank comes up holding it in every row, so `pen[1] = RED` works before any art
is loaded.

Black rather than transparent above 16, for two reasons. **You can see black.**
An index nobody authored draws as a black pixel rather than as nothing, so a
table that was never filled in is a thing on screen instead of art that
quietly failed to appear. And it makes alpha a function of the index from
birth, which §*The hole* shows is what keeps drawing and collision answering
the same question.

### Index 0 is the hole, and its alpha is pinned

Writing index 0 stores your RGB and forces alpha to 0, on every road in:
`bank[0][0] = ...`, `bank[0][0].alpha = 200`, a whole-row assignment, and
`bank[0][-256]`, which normalises to 0 so it cannot slip past as "not zero".

Every other index keeps exactly the alpha you wrote. There is no blanket rule
forcing colours opaque.

The one road around it is `texels`, which writes the array directly. An opaque
index 0 fills in every hole in the art at once.

## The bank

`PaletteBank` is 256 palettes as one `(256, 256, 4)` uint8 block — **256KB,
fixed**. No size argument, nothing to grow, nothing to shrink.

Fixed is what makes a row number always valid and a bank never reallocate.
Chun-Li, the largest thing in the tree, authors 28 tables per tileset across
three tilesets; everything above that is spare and is where derived shades go.
Which spare rows is the author's choice — the engine hands out none.

**A bank cannot tell you how many rows a package authored.** `len(bank)` is
256 whatever the file held, and an authored row is not marked, so a package
that ships one table where four were expected differs from a correct one only
in its contents. Check the contents, not the count: a loaded tileset's rows
0..n differ from each other and from the default the bank came up with.

One block rather than a list of tables, because that is the shape the GPU
takes: one 256×256 RGBA texture, a row per palette. That makes selecting a
palette a **number** the shader reads rather than a bind, and a bind would cut
a draw run.

### Addressing

    bank[row]              one palette
    bank[row][index]       one colour
    bank[40] = bank[0]     copy a row, 1.6us
    bank.texels            the whole block, for bulk work

`bank[row]` is a `Palette` **bound** to the bank — it holds a row number, not
a slice, so it stays valid for the life of the bank. `bank[row][index]` is a
`ColorQuad` bound to that slot: it holds no channels of its own, reads through
to the table and writes through it, so it cannot go stale and `.red = 0`
reaches the GPU.

Nothing else hands out the array. A view and a value look identical and behave
differently, and the difference lands on whoever assumed wrongly.

## ColorQuad

Four 8-bit channels, RGBA, and tuple-like: it unpacks, slices, indexes and
compares.

    quad.red, quad.green, quad.blue, quad.alpha
    r, g, b, a = quad
    quad[:3]
    quad == (255, 0, 0)      # a 3-tuple compares as opaque

Two roles, told apart by where it came from. One you **construct** is a value,
and writing a channel changes your value. One a palette **hands back** is bound
and writes through to the table. Only the repr distinguishes them.

Three channels is the normal spelling everywhere a colour is written — alpha
is decided by the index, so `(255, 0, 0)` means what it looks like.

**`color` is the spelling of the identifiers here**, and `colour` the
spelling of the prose. Not drift: the package has settled on the American
form for names, and the sweep that makes the rest of the tree agree is
`Normalise spelling across the engine`.

It lives at `blitspersecond.graphics.color`, outside the palette package,
because it depends on nothing there and a buffer or an atlas can name a colour
without importing tables. It is documented here because that is where a reader
looks for it.

## The register

Which row a layer draws through is a **register**, not a property of the
colours.

    bank.active = 7        the row this bank is drawn through
    bank.current           bank[bank.active], without saying it twice

Moving it recolours everything reading that bank at the next draw and changes
no table, so it does not dirty the bank. A whole colour scheme is a write to
this rather than a rewrite of anything.

`Console` and `TileMap` draw one thing each and use it directly.

**Sprites do not.** A sprite carries its own register per tileset, through
`sprite.palettes["character"] = 3`, because the register has to be per
instance: two sprites over one interned sheet share the bank and the texture
and must be able to point at different rows. The killing test is a mirror
match — red Chun-Li and blue Chun-Li are two sprites, two layers, two
registers, one set of tables and one upload. A register on the bank could not
express that.

## The modify path

Operators change the row they are called on and hand it back, so they chain.
A palette is a row of a bank rather than a value, so a derived table has
nowhere to go but a row somebody chose: put the source in the destination
first, then modify it.

    bank[40] = bank[0]
    bank[40].duotone(HAIR, 0.6).darken(0.5)

| arity | operators |
|---|---|
| nothing | `negate()` |
| scalars | `darken(k)`, `duotone(colour, mix)`, `cycle(start, stop, by)` |
| another palette | `blend(other, k)`, `multiply(other)` |

Alpha never appears in any of them. It is decided by the index, so the hole
survives every operator without any of them knowing about it.

**`duotone` and `blend` are not the same operation and neither can replace the
other.** `blend` moves entry toward entry, so a ramp blended toward a ramp
stays a ramp. `duotone` gives every entry the target's hue while keeping its
own luminance. Blending toward a flat colour instead drags dark pixels **up**
toward a bright signature — a white character's shadow comes out brighter than
she is, and the ramp is not even monotonic. That is why a fading trail uses
`duotone`, and why both exist.

**`cycle` is palette animation.** Rotating a span of indices is what makes
water, fire and waterfalls move: the art does not change and no pixel moves,
only which colour each index resolves to. It takes a range because a room
cycles a handful of indices while every other colour holds still.

There is no `Operator` base class. Arity is what the signature says, and
nothing overrides anything — see *Encode audio operator arity in types* on the
board for what a base class costs a type checker.

## Sharing, and how to stop

A bank handed to several holders is **one table**, and a write moves all of
them. That is the same law `PixelBuffer` applies to pixels, and it is
deliberate: scene-wide palette cycling is one bank installed on every layer
that should cycle together, and a sheet's bank is shared by every sprite
drawing that sheet.

`copy()` is how a holder stops sharing — **7.8µs** for a whole bank, an array
clone rather than a round trip. Nothing copies on write.

A sprite deriving shades into spare rows of its sheet's bank is writing where
every other holder of that sheet can see it. That is the law, not an
oversight; `bank.copy()` is the opt-out.

## The upload

The bank owns its texture, lazily created and dirty-gated, exactly as
`PixelBuffer` owns its own. A bank that is never drawn never touches GL, so
one can be built before there is a window, and a bank that dies takes its
texture with it.

Every write through the retail road flags the bank. `texels` cannot — numpy
writes are unobservable — so **set `dirty` yourself after bulk work**, the
same contract `PixelBuffer.pixels` carries.

The upload is a `glTexSubImage2D` straight off `texels` — **32µs** for the
whole bank. `Texture.blit_into` is the same call wrapped in an `ImageData` and
a `tobytes()`, and that wrapping is 27µs of pure conversion over an array
already in exactly the layout GL wants.

**Read `bank.texture` at the head of a layer's `composite`.** That is what
makes the upload happen once per frame per changed bank, before any quads. It
matters beyond the saving: an upload lands on whatever texture unit is
currently active, so one arriving mid-pass disturbs the unit the art is bound
to and forces a re-bind. At the head of composite there is nothing to disturb.

## The hole, and why drawing and collision agree

`indexed.frag` fetches `(index, row)` from the palette texture and **discards
when alpha is under 128**. Collision keys off the index: a mask is
`pixels != 0`, which is a comparison against zero rather than 256 lookups.

Those are two different questions — *was it drawn* and *is it solid* — and
they give the same answer only because alpha is a function of the index. The
index-0 pin is what holds that, and the black-opaque default is what holds it
before anything is written.

**A mid-alpha entry breaks it.** An index written with alpha 100 is discarded
by the shader and still reads as solid to the mask: invisible, and it collides.
Nothing refuses this, because a console layer legitimately wants alpha in a
palette and does not collide through its silhouette. Alpha 128..255 blends
through the compositor; under 128 vanishes.

Named collision planes are outside this entirely — they are authored geometry
and are free to disagree with what is drawn.

## Limits and costs

| | |
|---|---|
| colours per palette | 256, fixed |
| palettes per bank | 256, fixed |
| memory | 256KB per bank, always |
| GPU | one 256×256 RGBA texture per bank |
| channel range | 0..255; outside raises rather than wrapping |
| alpha | honoured everywhere except index 0, which is pinned to 0 |
| shader floor | alpha < 128 is discarded, not blended |

    a blank bank                        11.4 us
    copy() a whole bank                  7.8 us
    bank[a] = bank[b]                    1.6 us
    upload when dirty                   32.3 us
    reading .texture when clean          0.08 us

    negate                  3.2 us      multiply   6.0 us
    blend                   8.4 us      duotone    8.6 us

Operator costs are per row and are almost all numpy call overhead on 768
values, so dozens per frame is nothing. The same operations across a whole
bank are 324µs to 1ms — 2 to 6% of a frame — which is why the modify path is
row-shaped.

Loading is bulk work and belongs on `texels`: a palette written colour by
colour costs **887µs** against **2.5µs** written as an array, and Chun-Li's 84
tables load in 0.21ms rather than 75ms.

Every refusal, by name:

| | |
|---|---|
| a channel that is not a whole number | `TypeError` |
| a channel outside 0..255 | `ValueError` |
| a colour that is not `(r,g,b)`, `(r,g,b,a)` or a `ColorQuad` | `TypeError` |
| a row assignment that is not 256 colours | `ValueError` |
| `bank[0, 1]` — a bank is addressed by row only | `TypeError` |
| a row outside 0..255 | `IndexError` |
| a colour index outside 0..255 | `IndexError` |
| a strength outside 0..1 | `ValueError` — asking for 2.0 asked for something |
| `blend`/`multiply` against anything but a palette | `TypeError` |

## Settled alternatives

**A texture per palette.** Selecting one becomes a bind, and a bind cuts a
draw run. One texture with a row each makes selection a number the shader
reads from a varying — which is also what lets the row become per-quad data
later.

**A 256-vec4 uniform.** The shared indexed program has one uniform state, so
alternating layers stomp each other's table and every layer pays to restore
it: measured at ~0.15ms per indexed draw, every compose, against a bind of
nanoseconds. (Figure from the 2026-07-18 log, when the uniform was replaced.)

**A palette as a value you hold.** Then a derived table needs somewhere to
live, banks need to grow to hold it, growth reallocates and rebuilds textures,
and a bank shared by an interned sheet grows every time anything derives from
it. A fixed bank with spare rows removes the whole category.

## Still open

Nothing. The subject is settled; the outstanding work touching it is the
tree-wide sweep in *Normalise spelling across the engine*, which is not this
chapter's to carry.
