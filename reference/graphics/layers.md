# Layers

A layer is a **place in the draw order**, and in every subclass the content
that place shows. There is no wrapper and no separate scene object: a pillar
*is* its layer, so `Console(CharSet.ASCII8X12)` builds both the thing you draw
with and the thing that has a position in the stack.

    console = Console(CharSet.ASCII8X12)
    bps.layers.add(console)
    console.after(background)

`bps.layers` is the one draw order the engine has, and it is also **the
compositor**. It is walked twice per **game tick** — not per presented frame —
once before the game's tick and once after. This page is how those two walks
work, what a layer owes them, and why collision hangs off the same base class
as compositing.

[console.md](console.md) is the worked example of a pillar that is a layer.
[collision.md](collision.md) owns the masks and the query surface; what is
here is only the part of collision that belongs to *being a layer*.

---

## Limits and costs

### The order

| | |
|---|---|
| collection | one, built by the engine and lent out as `bps.layers`. No setter, so there is no second one |
| order | index 0 is background, last is foreground; the compositor walks it in list order |
| a layer's place | one draw order at a time. `add` binds it, `remove` unbinds it |
| `add` | places at the foreground. It does not build and it does not move |
| `remove` | leaves the layer built and off-screen, keeping its contents and tag |
| re-adding | goes to the foreground, not to where it was |
| `enabled` | drawing only, and the layer decides it — a disabled layer still prepares and still answers collision questions |
| `blend` | **off** by default |
| `collidable` | **off** by default |

### Costs

| | |
|---|---|
| an empty draw order | one clear, and an early return — the cost of a bare clear-and-swap, which is what the published pacing figures assume |
| a disabled layer | one `enabled` test, inside the layer — and its settle still runs |
| a hidden collidable layer | a full settle every frame: a map streams its camera, a console packs its planes |
| blend state | one GL call per *transition*, not per layer — the walk tracks what is currently set |
| `glBlendFunc` | set once per frame unconditionally; inert while blend is off, and cheaper than tracking whether it still holds |
| a layer with nothing to prepare | one `is None` test |
| reading a plane through `collision_offset` | 0.469µs against 0.481µs at zero — offsets are free, which is why masks never move for fine scrolling |

`prepare` is billed in **neither** published metric: `gameloop_ms` starts
after it and `commit_duration` after that. It is not invisible — the pacer's
`observe_work` spans the whole tick body — but it is the engine's work and
billing it to the game would make a scene that animates read as one that
computes. `compose_ms` reports `commit_duration`, which is the clear, the
draw walk, and the settle half of every layer's `composite`.

### What refuses

The theme is that a reorder which cannot happen must say so. A silently
ignored ordering call produces a frame that is merely subtly wrong, which is
the worst failure available here — nothing reports it, and the search starts
at the drawing rather than at the ordering.

| | |
|---|---|
| `add` of a layer already in this order | `ValueError` — placing and repositioning are different questions; use `first`/`last`/`before`/`after` |
| `add` of a layer bound to another collection | `ValueError` — one layer, one draw order |
| `remove` of a layer that is not here | `ValueError` — not a quiet no-op |
| `remove` of a non-`Layer` | `TypeError` |
| reordering an unattached layer | `RuntimeError` — a position in an order you are not in is undefined |
| ordering against a layer in a different collection, or a non-`Layer` | `ValueError` / `TypeError` |
| `__setitem__` index out of range | `IndexError` |
| `enabled`, `blend` or `collidable` set to a non-bool | `ValueError` |
| `collidable = True` on art that cannot derive a mask | `ValueError`, saying why it is unbuildable rather than off |
| `collider()` on a layer that is not collidable | `ValueError` |
| `collider(name)` with an unknown plane | `KeyError`, listing what the layer does author |
| `collision_plane` on a layer that implements none | `NotImplementedError` |

---

## One object: the place and the content

The base class owns the **compositing** concerns — `enabled`, `tag`, `blend`,
stack position, with opacity and offset to come — and the **collision**
question. A subclass supplies the content and the geometry.

That fusion is the reason the ordering verbs read the way they do. A pillar is
a layer, so you name it directly:

    hud.before(console)
    actors.after(background)
    a.swap_with(b)

rather than reaching through it for something it holds. Each returns the layer
so calls chain, and each delegates to the collection, which is the single
source of truth for the order. A bound layer holds a back-reference to its
collection, set on `add` and cleared on `remove`.

**A layer knows only whether it is attached, not what it is attached to.**
`layer.attached` is a bool, and there is no `layer.layers` to reach through.
Handing the collection back would buy nothing — the engine has exactly one
`Layers`, so the answer is `bps.layers` in every real program — while opening a
sideways route: reaching through one layer to reorder or remove *others*, which
reads as though a layer had authority over the order it merely sits in.
Repositioning *this* layer is what the fluent verbs are for.

**`Layer` is not exported from `graphics`**, though its API is public. A game
constructs a pillar and calls a layer's methods constantly; what it never does
is name the base class. Exporting it would invite `class MyLayer(Layer)`, and
a bare `Layer` composites nothing — the content is what the subclass *is*.

## What `Layers` owns, and what it does not

Membership, order, and the frame. `add` and `remove` place and unplace;
`prepare` and `commit` are the frame. A layer arrives built by its pillar and
drawing nowhere; `add` gives it a place, and that is the whole of what `add`
does.

**It does not build layers and it does not present them.** Building belongs to
the pillar; presenting belongs to the display. What is left in the middle —
who is in the order, what order they are in, and what happens to them each
tick — is one job, and this is it.

**Adding an already-added layer raises rather than sending it to the front.**
Placing and repositioning are different questions asked in different words,
and the reordering verbs say where they are putting it. If `add` also meant
send-to-front, the two spellings for one intent would disagree about whether
the layer had to be somewhere else already.

**`Layers` imports no layer class**, and that is what keeps this file free of
every pillar: a fourth kind of layer needs no line here, and there is no
import cycle through `graphics` to defer around. A factory on the collection
would name all three pillars in its signatures — see
[Not a factory on the collection](#not-a-factory-on-the-collection).

`find(tag=...)` returns matching layers in stack order, or `None` rather than
an empty list, so "nothing has this tag" is distinguishable from a caller who
forgot to check before indexing.

## The frame

The engine calls two methods either side of the game's tick, and names no
per-layer step itself:

    layers.prepare()                  # BEGIN
    game_loop()                       # the game mutates
    layers.commit(display.pending)    # COMMIT
    display.frame_complete()          # the display decides it counts

**`prepare` brings every ATTACHED layer up to date**, so the game reads a
world that is already current: a sprite's pose and a console's echoed line are
this tick's when the game looks at them, the same guarantee the input cut
gives one line earlier.

**`commit` writes the buffer it is handed and nothing else.** It does not flag
the frame finished, does not know a second buffer exists, and takes no part in
showing this one. Swapping, promoting, presenting and deciding when a written
frame counts as done are the display's.

    clear the buffer to `background`
    if the order is empty: stop here
    with the buffer as target:
        set the blend func once
        for each layer, background to foreground:
            toggle blend if this layer differs from the last
            layer.composite()

**A commit has two halves and only one of them is gated.** `Layer.composite`
settles first and draws second:

    def composite(self):
        self._settle_collision()      # the world half, every attached layer
        if self._enabled:
            self._composite()         # the view half, only what is drawn

The settle brings a layer's collision geometry in line with the frame being
painted — a tilemap streams its camera, a sprite recomposes a moved plane, a
console packs its dirty rows. It is what makes the answer a query gets next
tick a fact about a frame that was really composed, rather than one assembled
mid-tick out of whatever had been touched by then. See
[The settle half](#the-settle-half).

**The render space is the buffer's, not the compositor's.** `as_target()`
binds the buffer and declares a 1:1 ortho over its own size with a matching
viewport — re-established each time, because the present pass sets a
window-sized viewport on its way out. Nothing that composes needs to know a
window exists.

**The empty case is exactly a clear and a swap**, deliberately. The
frame-pacing figures in [pacing.md](../engine/pacing.md) were all measured
against a clear-and-swap, and they stay comparable only while a game with no
layers still costs that and nothing more.

Each layer syncs its own GPU state and issues its own draw. The compositor
sequences the pass; it does not render — see [Not a renderer](#not-a-renderer).

**`pending` is read, never kept.** There are two buffers, alive for the
process, and a present that promotes exchanges the two references. The order
makes that moot rather than dangerous: the pacer swaps and only then issues
the tick, so `commit` resolves the property fresh and always gets the buffer
that just stopped being shown.

## Blend is a layer's, and it is off

`layer.blend` is read per layer during the walk, and defaults to `False`.

**That is the engine's model, not a performance default.** BPS paints back to
front with no depth test and no resolve, so colour is last-writer-wins: a
pixel is drawn or it is not. Indexed art carries that through — index 0 is
transparent, everything else is opaque, and `indexed.frag` *discards* index 0
rather than blending it away. A sprite layer over a tilemap composes correctly
with blending off, because transparent texels never reach the blender.

So translucency is a **per-layer effect** and this is where it lives: a
see-through overlay, an additive flash, a fog sheet over the world. What it is
not is a way to fade one sprite among many — that is a palette row, which
costs a number rather than a blend, and is what the hardware this engine
models actually did.

The walk tracks the current state and toggles only on a transition, so a stack
of non-blending layers pays one `glDisable` at most. `glBlendFunc` is set once
per frame regardless: it is inert while blend is off, and setting it beats
tracking whether it still holds — the present pass disables blend again for
its own quad, so even this much has to be re-established per frame rather than
at startup.

## Collision is on the base

Not on the pillars. **A layer is one texture, one draw call, and one
answerable collision identity**, so `collides_with` means the same thing
between two tile layers, a sprite and a console, or either against the other
— and there is one statement of it rather than one per pillar that can drift.

What a subclass supplies is the geometry, through four members:

| | |
|---|---|
| `_collision_refusal()` | why this layer may never be collidable, or `None` if it may |
| `collidable` | the switch, off by default |
| `planes` | the named planes the art authored; empty is the common case |
| `collision_plane(name)` | one packed screen-space plane |
| `collision_offset` | where screen `(0, 0)` sits inside those planes |

The bare base supplies none of them, so an ordinary layer **refuses the
question loudly** instead of answering `False` about a mask it never had.

**Nothing here answers False.** A layer that is out of collision has no answer
to give, and a `False` would be indistinguishable from "these two do not
touch" — which is how a level ships with its hazards silently inert. Both
gates fire in `collider()`, the call that named the thing, rather than inside
an `.at()` further down the frame; an unknown plane name raises too, because a
typo'd hitbox that never hits is the same bug wearing a costume.

The two refusals read differently on purpose. Art that can never collide wants
authoring differently; a switch you turned off wants turning back on.

**Collision does not read `enabled`.** Nor does the compositor: the layer
itself consults it when asked to composite, and the collision gates never look
at it at all, so a layer turned off keeps answering questions. It is the same
split a sprite makes one level down between `visible` and `collide`: what is
drawn is a view decision, what is solid is a world one. To take a layer's
contents out of play, clear their collide bits rather than hiding the layer.

The two flags are separate axes, and neither is a shortcut for the other. See
[Which flag is read where](#which-flag-is-read-where).

### The settle half

That split is mechanical rather than a convention each pillar keeps.
`Layer.composite` calls `_settle_collision` on every attached layer and gates
only `_composite` on `enabled`, so a layer cannot be hidden into staleness by
a pillar that forgot the rule.

**The case that forces it is a collider layer nobody sees** — the ladders and
ledges a map author paints for the engine rather than the player. It scrolls
with everything else, so its plane has to keep moving while nothing draws it.
A settle behind `enabled` would leave that layer answering about wherever the
camera was when it was last drawn, which for a never-drawn layer is never.

**It is not gated on `collidable` either**, though that would save a little on
a layer that is neither drawn nor solid. A layer must behave the same whether
or not it is visible, and the only difference allowed is that its pixels do
not reach the framebuffer. Gating here would make hidden-and-uncollidable a
third case, with a camera that froze while the layer was away and caught up
when it came back — two behaviours where the whole point is that there is one.
So a hidden, non-collidable tilemap still streams: one camera compare, and
whatever a coarse step drags with it.

**The settle is the only road to that work.** Each pillar split it out of the
sync at the top of its own `_composite`, so the draw path does not reach it
and every layer settles exactly once, drawn or not. Had the two paths both
kept a copy, a hidden layer and a visible one would be running different code
to reach the same state, which is the arrangement that drifts.

**Plane keys are opaque, including their type.** A sprite sheet authors
strings — `attack`, `body_hurt`; a console authors the four glyph plane
numbers, because a font has no names to give. A key is anything hashable and
the engine reserves no spelling.

`collision_offset` is one number carrying two things — a stored-to-screen
border and a sub-word camera remainder — because they are the same kind of
thing, a constant displacement between where a bit is stored and where it
appears. Splitting them would mean two things to keep in step. It exists so a
mask never has to move for fine scrolling: reading through an offset is free
(0.469µs against 0.481µs at zero, inside noise), where shifting the plane
would cost tens of microseconds per layer per frame.

## Which flag is read where

A layer carries four pieces of state that something else consults, and they
are read in different places at different times. Keeping them straight is the
difference between "turned off" and "has no answer".

| flag | read by | when | effect |
|---|---|---|---|
| `enabled` | `Layer.composite` | every tick, by the layer itself | the layer draws nothing — its settle still runs |
| `blend` | `Layers.commit` | every tick, per layer | GL blend is toggled, on transitions only |
| `collidable` | `Layer._require_collidable` | when a collider is minted, and again when a question is asked | raises if off — it never answers `False` |
| `tag` | `Layers.find` | only when asked | — |

**Compositing never reads `collidable`, and collision never reads `enabled`.**
A layer can draw and not collide, collide and not draw, both, or neither. And
**`prepare` reads none of them** — see below.

### Two walks, one owner

`Layers` owns both, and they differ in which layers they visit:

| walk | calls | does |
|---|---|---|
| `prepare` | `layer.prepare()` | see [The prepare slot](#the-prepare-slot) |
| `commit` | `layer.composite()` | clears, manages blend, walks |

**Both visit every layer in the order, and neither reads a layer's state.**
The compositor sequences; it does not interpret. `enabled` is consulted inside
`Layer.composite`, by the layer it belongs to — a walk that skipped disabled
layers would be a second place that knows what the flag implies, and the two
would drift.

That is also why the walk can be unaware that a commit has two halves.
`composite` settles every attached layer and draws the enabled ones, and the
compositor calling it once is what keeps the ungated half from needing a
third walk of its own — see [The settle half](#the-settle-half).

**Attachment is the statement of participation.** A layer you do not want
processed is one you do not add — so `enabled` means exactly one thing,
whether the layer is drawn, and a hidden layer keeps advancing its clips and
keeps repainting a typed line. Hiding is a view decision and never a pause.

Both run once per game tick, either side of the game's callback.
Composition bills at game rate whatever the panel is doing; what happens per
presentation slot is `present`, which puts an already-composed frame up and
may show the same one twice.

`Display` holds no draw order at all. It hands over a buffer and decides when
a written frame counts as finished; it never looks at a layer.

#### The prepare slot

`Layer.prepare` is not a sprite concern. Two pillars implement it, for
unrelated reasons:

| pillar | what it does | behind the slot |
|---|---|---|
| sprite | moves a playing clip on, and applies the new pose | `_update_animations()` |
| console | repaints the line being echoed | `_repaint_input()` |
| tilemap | nothing | — |

**One slot, and the walk knows nothing else.** Each pillar names its own work
honestly; the compositor reaches it only through `prepare`. Probing for
pillar-specific names would make those names an interface without declaring
one — a shadow API, discoverable only by reading the walk — and a fourth
pillar would have to reverse-engineer the contract. It is a real method rather
than a `getattr` probe for the same reason: typed, greppable, and not
satisfiable by accident by an unrelated attribute of the same name.

**It runs before the game's tick**, which is what makes the world the game
reads coherent. That does not delay anything a game starts *during* its tick:
`Sprite.play` applies its own first frame immediately, precisely so a `play()`
issued this tick shows this tick rather than leaking a frame of the previous
pose.

What it costs shows up in no published metric — see [Costs](#costs).

## What it is not

### Not an engine that produces a layer

The shape a reader might expect is an engine object that mints a layer you
then add — `bps.layers.add(engine.layer)`, with the engine as the thing you
hold. The pillars are the layers instead, and the reason is convergence rather
than convenience: the three pillars spelling the same idea the same way is
what makes learning one teach the others. A `.layer` to unwrap gives every
pillar its own dialect for the same sentence, and gives `add` a generic
signature that can hand back the wrong object — worked through in
[console.md](console.md#what-it-is-not).

### Not a factory on the collection

`bps.layers` does not mint layers: there is no `tile()`, `sprite()` or
`glyph()` on it. The cost of putting them there is structural — the collection
would name all three pillars in its signatures, and would have to import them
inside method bodies to dodge an import cycle through `graphics`. Building
belongs to the thing being built. The collection places what it is handed and
knows about no pillar at all.

### Not a renderer

`Layers` composites; it does not render. It sequences the pass — the clear,
the walk, the blend state — and each layer issues its own draw. It does not
own the projection either: the buffer declares its own render space when
drawn into. So the whole of its GL is one `glBlendFunc` and a pair of
enable/disable calls.

Folding the drawing in looks attractive, because the pillars' draw paths are
nearly identical: `Console._composite` and the tilemap's path match on eleven
statements exactly — `program.use()`, the texture unit assignments, the
three-line palette bind and back, the sheet bind, the blend calls,
`program.stop()`. **Exactly one line differs: the draw itself.**

That one line drags an apparatus behind it. The tilemap needs a VAO, an index
buffer, four generic attribute writes and a UV flush; the console has no
analogue for any of it, because its geometry is one quad. A compositor owning
the draw would own all of that for one pillar and none of it for the other.

The related question — whether a *layer* should own a static renderer rather
than being one — splits by whether the GPU-side state is per-instance. Every
console composes to 640x360 whatever its charset, so one shared quad could
serve every console there will ever be. A tilemap's VBOs *are* its data, so a
static renderer would key them by layer, which is the same allocation with a
lookup in front of it.

### Not a collection a layer can reach

See `attached`, above. A layer reports whether it has a place; it cannot hand
out the order it sits in.

## Still open

- **Whether `add` returns the layer it was given.** It returns `None` today
  and refuses anything that is not a `Layer`, which is what makes the
  wrong-object trap impossible. A board card argues it should hand the layer
  back so `console = bps.layers.add(Console(...))` is available to anyone who
  wants it, with the named two-line form staying the documented one — the
  return being safe precisely because `add` refuses anything that is not a
  `Layer`. Both
  positions are written down and they are not the same rule.
- **RESET has a name for half of what it covers.** The frame is a
  transaction — prepare, the game, commit — and the fourth phase, leaving
  each layer in a known state for next time, was until recently happening
  wherever was convenient. `_settle_collision` is now that phase for the
  world half, and it is a real declared slot: it runs at the commit and
  settles what the NEXT tick will ask about. What is still unnamed is the
  view half — a dirty band cleared once recomposed, a plan rebuilt — which
  each pillar tidies inside its own `_composite`. Commit and reset are
  sequential, so whether the rest earns a second slot or stays folded in is
  open. Nothing is wrong; part of it is unnamed.
- **Opacity and offset** are named in the base class as compositing concerns
  and are not built.
