"""Collision: the vocabulary every pillar answers the same question in.

    if player.collides_with(ladders).at(x, y, w, h):
        ...

**This is not tile code, and it is not sprite code.** It is here, beside the
pillars rather than inside one, because the engine's premise is that *a layer
is one texture, one draw call, and one answerable collision identity* -- so a
tile layer, a sprite layer and a console mask answer the IDENTICAL question,
and cross-engine collision falls out for free. An actor against terrain,
against ladders, against painted triggers and against another actor is one
protocol throughout. Burying it in the pillar that happened to need it first
would make a cross-pillar contract look like that pillar's machinery.

What lives here is the shared half only: the bit layout, the participant, and
the pairwise question. **The masks themselves belong to the pillars**, because
how a plane comes to be stamped is nothing the other pillars can use -- a tile
layer pushes stamps as it places quads and shifts the whole plane when the
camera moves, while a sprite layer recomposes every plane from nothing every
frame. They share a layout and a question, and no mechanism.

    packed.py    the bit layout: one bit per pixel, DWORD words, and the
                 pack/unpack primitives that speak it
    kernel.py    collide_any: the ONE place bits are shifted -- compiled,
                 because a hitbox is all dispatch overhead in NumPy
    collider.py  Collider: one layer's geometry, optionally one named plane
                 of it -- what a question is asked OF
    query.py     CollisionQuery: the prepared pairwise question and the
                 windowed AND that answers it

## No entities, no shapes, no physics

The engine produces masks and answers overlap. It does not know what a plane
MEANS: `attack`, `body_hurt` and `throwable` are opaque keys the art supplies,
and the engine reserves none of them. Damage, push, throws and reactions are
game-side decisions, and an engine with opinions about them would be a
fighting-game engine rather than one you could write a platformer in.

## The default plane is the heritage path

`boss.collides_with(ship).at()` is pixel-perfect with zero configuration,
because every collidable layer has a default plane and it is the silhouette of
everything solid in it. Named planes are opt-in refinement on top. A game that
never authors one never types the word `collider`.

All three pillars fill their planes at the commit, through
`Layer._settle_collision`, so a question answers about the frame that was
painted. How each one fills is that pillar's business and its own chapter.

Reference: `bps/reference/graphics/collision.md`, which owns the layout, the costs,
the query surface and the case for compiling the kernel.
"""

from .collider import Collider, as_collider
from .kernel import collide_any, stamp_many
from .packed import (
    CLOW,
    CWORD,
    CWORD_BITS,
    CWORD_SHIFT,
    pack,
    pack_store,
    unpack,
    words_for,
)
from .query import CollisionQuery

__all__ = [
    "CLOW",
    "CWORD",
    "CWORD_BITS",
    "CWORD_SHIFT",
    "Collider",
    "CollisionQuery",
    "collide_any",
    "stamp_many",
    "as_collider",
    "pack",
    "pack_store",
    "unpack",
    "words_for",
]
