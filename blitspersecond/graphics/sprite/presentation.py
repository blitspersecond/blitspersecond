"""Presentations: the drawings a `Sprite` makes of itself.

    ghosts = chun.before.extend(4)      # a trail, drawn behind
    smear  = chun.after.append()        # drawn in front
    ghosts[0].palette_index["character"] = 3     # a dimmer row: a trail fades

One sprite, N drawings of it. They are ONE identity -- one texture, one draw
call, one answerable collision question -- so they are not sprites and do not
get a layer each.

## Identity and draw order are two different things

**A slot id says WHICH presentation. A chain says WHERE it draws.**

    q q q q Q q q q q
    |_____| ^ |_____|
     before |  after
          slot 0

**That is ONE sequence, not three**, and it is STORED pre-split because slot 0
is exactly where a bulk gather has to break anyway: it is the one position
that may expand to several quads and cross to another tileset. A single order
array would have to find that position and split around it every frame. Two
arrays are that split, kept rather than rediscovered -- so do not merge them.

A slot id is stable for the presentation's life. Reordering never touches it,
and never touches the state arrays.

## Liveness is NOT linkage

    visible / collide   per-frame: spawn, reap, park. Never relinks.
    append / remove     authoring the order. Setup, or a rare change.

**A dead bullet is a parked slot, not a removed one.** Reaping by `remove()`
is an O(n) memmove per corpse and destroys the thing the arrays exist for;
reap with `visible[dead] = False` and respawn into `flatnonzero(~visible)`.
The two stay distinct because the cheap one has to read like the obvious one,
and reaching for the wrong one costs a lot with nothing announcing it.

## What the primary has that the others do not

| the primary only | every presentation |
|---|---|
| the clip cursor | position, orientation, assembly |
| multipart assemblies | visible, `collide` |
| named collision planes | mask stamping |

Instanced presentations are additionally pinned to `sheet.primary_tileset`,
which makes the whole block one texture under one bank. **The multipart
restriction is what pays for that**: every instanced presentation is exactly
one quad, so four and twenty-five thousand are the same shape.

Why the split, what a linked list would cost here, and how a population is
edited: `bps/reference/graphics/sprites.md`, "Presentations".
"""

from __future__ import annotations

from contextlib import contextmanager
from math import floor
from typing import TYPE_CHECKING, Optional, Tuple, Union

import numpy as np

from blitspersecond.common import Location

from ..orientation import Orientation
from .internal import PaletteRegisters

if TYPE_CHECKING:
    from .sheet import Assembly
    from .sprite import Sprite


#: No assembly presented. Not an error -- see `Presentation.assembly`.
NO_ASSEMBLY = -1


#: Slot 0 is the primary, always allocated, never freed.
PRIMARY = 0


class Slots:
    """The per-slot state arrays, indexed by slot id, plus a free list.

    A slot id is an index into every array here and is stable for the
    presentation's life. It says nothing about draw order -- that is the
    chains' business -- so freeing and reusing an id never disturbs anything
    drawn.

    Capacity doubles when the free list runs dry; existing ids keep their
    rows, because growth copies rather than reindexes.

    Internal. Reached through `Sprite`, never named by a game.
    """

    __slots__ = (
        "_capacity",
        "_free",
        "_alive",
        "position",
        "orientation",
        "assembly",
        "visible",
        "collide",
        "palette_index",
    )

    def __init__(self, default_index: int, capacity: int = 8) -> None:
        self._capacity = max(2, int(capacity))
        self._allocate(self._capacity, default_index)
        # Free ids ASCENDING, in an array rather than a list, so a bulk take
        # is a slice instead of 25,000 `pop()` calls -- and so a fresh swarm
        # gets CONTIGUOUS ids, which keeps its gather sequential.
        #
        # Slot 0 is never in here: the primary is a sprite's floor, not an
        # allocation.
        self._free = np.arange(PRIMARY + 1, self._capacity, dtype=np.int32)
        self._alive = np.zeros(self._capacity, dtype=bool)
        self._alive[PRIMARY] = True
        self.visible[PRIMARY] = True
        self.collide[PRIMARY] = True

    def _allocate(self, n: int, default_index: int) -> None:
        self.position = np.zeros((n, 2), dtype=np.int32)
        self.orientation = np.zeros(n, dtype=np.uint8)
        self.assembly = np.full(n, NO_ASSEMBLY, dtype=np.int32)
        # A fresh slot is parked, not shown -- allocating must not put art on
        # screen. The primary is the one exception; a lone sprite draws.
        self.visible = np.zeros(n, dtype=bool)
        self.collide = np.zeros(n, dtype=bool)
        self.palette_index = np.full(n, default_index, dtype=np.int32)

    @property
    def capacity(self) -> int:
        return self._capacity

    def live(self, slot: int) -> int:
        """Check a slot id and hand it back, so callers read as `slots.live(n)`."""
        if not (0 <= slot < self._capacity and self._alive[slot]):
            raise IndexError(
                f"slot {slot} is not a live presentation of this sprite "
                f"({int(self._alive.sum())} are). Slot ids come from "
                f"`sprite.before.append()` and friends, and a removed one is "
                f"not reused until something else asks for a slot."
            )
        return slot

    def take(self, count: int, palette_index: int, assembly: int) -> np.ndarray:
        """`count` fresh slot ids, grown into if the free list is short.

        **Seeded from the primary, which is the template.** A presentation is
        another drawing of THIS sprite, so a new one arrives looking like it:
        the primary's position, orientation, pose and palette register.

        Colour is not in that list and has not been since 2026-09-02, because
        an instanced presentation has no colour of its own to seed. The
        sprite's registers apply to every drawing of it. See `Presentation`. Kris: *"taking sensible defaults from the template
        master makes sense; nothing stopping you changing them."*

        The alternative -- resetting to the tileset's own defaults -- put a
        white-haired character's afterimages in the default red, silently and
        with nothing raising. That is the shape of failure worth designing
        out: a wrong colour is not an error anywhere, it is just wrong.

        **Both presence bits stay off.** `visible` and `collide` are NOT
        inherited, because allocating capacity must not put art on screen or
        a hitbox in the world. A new presentation is PARKED, and looks like
        the sprite the moment it is shown.

        A reused id is reseeded the same way, so it cannot arrive carrying
        the position of whatever held it before.
        """
        if self._free.size < count:
            # To fit IN ONE STEP, not by repeated doubling: a swarm asking
            # for 25,000 at once would otherwise copy every array twelve
            # times on the way up. Still at least a doubling, so a caller
            # appending one at a time keeps amortised growth.
            needed = self._capacity + (count - self._free.size)
            self._grow(palette_index, max(needed, self._capacity * 2))
        taken, self._free = self._free[:count], self._free[count:]
        # A contiguous take seeds by SLICE, not by fancy index. Ids come off
        # an ascending free array, so a fresh population is contiguous and
        # this is the mainline -- and it is worth detecting: at a million
        # presentations the scatter costs 43 ms and the slice 1.5 ms.
        #
        # `ptp` is max-minus-min, which settles it exactly: the ids are
        # distinct, so n of them spanning n-1 can only be a solid run. Two
        # vectorised passes to decide, against a 28x difference.
        where: object = taken
        if count and int(np.ptp(taken)) == count - 1:
            first = int(taken.min())
            where = slice(first, first + count)
        self._alive[where] = True
        self.position[where] = self.position[PRIMARY]
        self.orientation[where] = self.orientation[PRIMARY]
        self.assembly[where] = assembly
        self.visible[where] = False
        self.collide[where] = False
        self.palette_index[where] = palette_index
        return taken

    def give_back(self, slot: int) -> None:
        """Return a slot id to the free list. The primary cannot be given."""
        if slot == PRIMARY:
            raise ValueError(
                "the primary presentation cannot be removed -- it is not a "
                "member of the draw order, it is the position the draw order "
                "is read relative to. `before` and `after` mean earlier and "
                "later THAN THIS, so removing it would leave the other "
                "presentations with nothing to be ordered around. Park it "
                "instead: `visible = False` draws nothing and keeps the "
                "sprite's position, pose and registers where they are."
            )
        self._alive[slot] = False
        self.visible[slot] = False
        self.collide[slot] = False
        # At the FRONT, so a freed id is reused before fresh capacity is --
        # which keeps a churning population inside the range it already
        # touched rather than walking off into cold memory.
        self._free = np.concatenate(
            (np.array([slot], dtype=np.int32), self._free)
        )

    def _grow(self, default_index: int, new: int) -> None:
        """Grow to `new`, keeping every id on its own row.

        Growth copies rather than reindexes, so a slot id survives it and
        every handle held across it stays valid.
        """
        old = self._capacity
        keep = (
            self.position,
            self.orientation,
            self.assembly,
            self.visible,
            self.collide,
            self.palette_index,
        )
        self._allocate(new, default_index)
        for name, previous in zip(
            (
                "position",
                "orientation",
                "assembly",
                "visible",
                "collide",
                "palette_index",
            ),
            keep,
        ):
            getattr(self, name)[:old] = previous
        alive = self._alive
        self._alive = np.zeros(new, dtype=bool)
        self._alive[:old] = alive
        self._capacity = new
        self._free = np.concatenate(
            (self._free, np.arange(old, new, dtype=np.int32))
        )


class Chain:
    """One side of the draw order: an array of slot ids, back to front.

        chun.before.append()            # one more, drawn furthest back
        chun.before.extend(4)           # four, in order
        chun.after.prepend()            # just in front of the sprite

    `before` draws first and so appears BEHIND the sprite; `after` draws last
    and appears in front. Within a chain, later in the array draws later --
    the same direction the layer stack above runs.

    **Append and prepend say where, not which.** The slot id you get back is
    an identity and carries no ordering meaning, so a presentation can be
    moved without its state moving with it.

    Minted on access, so a chain is always a live view. Holding one is
    holding a reference to the sprite and which side you meant.
    """

    __slots__ = ("_sprite", "_after")

    def __init__(self, sprite: "Sprite", after: bool) -> None:
        self._sprite = sprite
        self._after = after

    @property
    def _order(self) -> np.ndarray:
        return self._sprite._after if self._after else self._sprite._before

    def _write(self, order: np.ndarray) -> None:
        if self._after:
            self._sprite._after = order
        else:
            self._sprite._before = order
        self._sprite._touch()

    def __len__(self) -> int:
        return int(self._order.size)

    def __iter__(self):
        return (Presentation(self._sprite, int(s)) for s in self._order)

    def __getitem__(self, position: int) -> "Presentation":
        """The presentation at a POSITION in this chain.

        Ordinary sequence indexing, negatives included: `chun.before[-1]` is
        the one drawn last within the chain, so the one nearest the sprite.
        A chain is a sequence -- unlike `sprite[slot_id]`, which is an
        address.
        """
        return Presentation(self._sprite, int(self._order[position]))

    def append(self) -> "Presentation":
        """One new presentation at the far end -- furthest back in `before`,
        furthest forward in `after`. Arrives parked.

        **Appending in a loop is quadratic**, because each one concatenates
        the order array: 2000 of them is ~30 ms. That is the cost of the
        order being an array rather than a chain, and it is the right trade
        while reordering is a setup-time act -- but ask for them all at once
        with `extend(n)`, which is one allocation and one concatenate.
        """
        return self.extend(1)[0]

    def prepend(self) -> "Presentation":
        """One new presentation at the near end. Arrives parked."""
        slot = self._take(1)
        self._write(np.concatenate((slot, self._order)))
        return Presentation(self._sprite, int(slot[0]))

    def extend(self, count: int) -> "Presentations":
        """`count` new presentations at the far end, in order.

        The bulk door, and the one a swarm uses: `fountain.after.extend(
        25_000)` is one allocation and one concatenate, not 25,000 calls.
        """
        count = int(count)
        if count < 0:
            raise ValueError(f"cannot extend by {count} presentations")
        slots = self._take(count)
        self._write(np.concatenate((self._order, slots)))
        return Presentations(self._sprite, slots)

    def insert_before(self, presentation: "Presentation") -> "Presentation":
        """A new presentation drawn immediately before an existing one, so it
        appears behind it. Arrives parked."""
        return self._insert_at(self._position_of(presentation))

    def insert_after(self, presentation: "Presentation") -> "Presentation":
        """A new presentation drawn immediately after an existing one, so it
        appears in front of it. Arrives parked."""
        return self._insert_at(self._position_of(presentation) + 1)

    def remove(self, presentation: "Presentation") -> None:
        """Take a presentation out of the draw order and free its slot.

        **This is not how a swarm reaps.** A dead bullet sets `visible` and
        `collide` False and keeps its slot, so the arrays stay put and the
        next spawn reuses it. Removing is for changing the draw order, which
        happens at setup or rarely -- doing it per frame is an `O(n)` memmove
        per corpse.
        """
        at = self._position_of(presentation)
        self._write(np.delete(self._order, at))
        self._sprite._slots.give_back(presentation.slot)

    def _take(self, count: int) -> np.ndarray:
        """Allocate, seeded from the primary -- see `Slots.take`.

        The two values the pool cannot read for itself are resolved here. The
        primary's palette register lives in its per-tileset dict rather than
        in the slot array, because only the primary has more than one; and its
        pose is inherited only if an instanced presentation could legally hold
        it, since a multipart or off-tileset pose is refused everywhere but
        the primary and seeding one would plant state no setter would have
        allowed.
        """
        sprite = self._sprite
        return sprite._slots.take(
            count,
            sprite._slot0_palette_index[sprite.sheet.primary_tileset],
            sprite._inheritable_assembly(),
        )

    def _insert_at(self, position: int) -> "Presentation":
        slot = self._take(1)
        self._write(np.insert(self._order, position, slot))
        return Presentation(self._sprite, int(slot[0]))

    def _position_of(self, presentation: "Presentation") -> int:
        found = np.flatnonzero(self._order == presentation.slot)
        if not found.size:
            side = "after" if self._after else "before"
            raise ValueError(
                f"slot {presentation.slot} is not in this sprite's {side} "
                f"chain, so there is no position in it to work from"
            )
        return int(found[0])

    def __repr__(self) -> str:
        side = "after" if self._after else "before"
        return f"<Chain {side} {[int(x) for x in self._order]}>"


class Presentations:
    """The presentations one `extend()` made -- and the bulk surface a swarm
    is actually driven through.

        swarm = fountain.after.extend(25_000)
        swarm.assemblies[:] = fountain.assembly_id("blue")
        with swarm.edit():
            swarm.positions[:, 1] += 1
            swarm.visible[live] = True

    **Arrays, not a scalar bridge.** Reaching every presentation through its
    own property setter costs 21.8 ms at 25,000 against 3.1 ms for the same
    work written into the arrays -- 1.3 frames against a fifth of one.
    Reference put it plainly: *"Position and liveness ARE the renderer's dense
    instance state: simulation writes them directly and the engine projects
    the whole pool into quads with NumPy. No 5,000-property scalar bridge."*

    Small `n` is served perfectly well by `Presentation`; this exists because
    the swarm case is unreachable without it.

    ## The views are real views, or you get an error

    A batch's slot ids are contiguous when it is freshly made, so these hand
    back genuine NumPy VIEWS into the state arrays and writes land. After
    removals have fragmented the free list a batch's ids may not be
    contiguous, and a fancy-index would return a COPY: every write silently
    swallowed, exactly the hazard reference's own `SpritePool.orientation`
    docstring names from the inside. So a fragmented batch raises rather than
    handing back something that looks like it works.
    """

    __slots__ = ("_sprite", "_slots")

    def __init__(self, sprite: "Sprite", slots: np.ndarray) -> None:
        self._sprite = sprite
        self._slots = slots

    @property
    def slots(self) -> np.ndarray:
        """The slot ids, in the order they were made."""
        return self._slots

    @property
    def contiguous(self) -> bool:
        """Whether this batch can hand out array views -- see the class note."""
        n = self._slots.size
        return not n or int(np.ptp(self._slots)) == n - 1

    def _span(self) -> slice:
        if not self.contiguous:
            raise ValueError(
                "this batch's slots are not contiguous, so an array view "
                "would be a copy and every write would be silently lost. Its "
                "ids came from earlier removals; allocate a swarm in one "
                "`extend()` before removing anything, or drive these through "
                "`Presentation` one at a time."
            )
        first = int(self._slots[0]) if self._slots.size else 0
        return slice(first, first + self._slots.size)

    @property
    def positions(self) -> np.ndarray:
        """`(n, 2)` int32 view of where each one is, in whole pixels.

        **Integer, because the engine draws whole pixels.** A simulation
        integrating sub-pixel velocity keeps its own float arrays and floors
        on the way in -- one vectorised `np.floor`, once, rather than the
        engine guessing what the fraction was for.
        """
        return self._sprite._slots.position[self._span()]

    @property
    def orientation(self) -> np.ndarray:
        """`(n,)` uint8 view of each one's orientation, as the flag value."""
        return self._sprite._slots.orientation[self._span()]

    @property
    def assemblies(self) -> np.ndarray:
        """`(n,)` int32 view of which pose each one presents, as ids.

        Ids come from `Sprite.assembly_id`, which validates the NAME once, so
        a bulk assignment of 25,000 is one check and one broadcast rather than
        25,000 checks. `NO_ASSEMBLY` presents nothing.
        """
        return self._sprite._slots.assembly[self._span()]

    @property
    def visible(self) -> np.ndarray:
        """`(n,)` bool view: the liveness bit a swarm spawns and reaps
        through. `np.flatnonzero(~visible)` is the free list."""
        return self._sprite._slots.visible[self._span()]

    @property
    def collide(self) -> np.ndarray:
        """`(n,)` bool view: whether each one stamps the collision planes."""
        return self._sprite._slots.collide[self._span()]

    @property
    def color(self) -> np.ndarray:
        """Gone. A swarm has no colour of its own -- see `palette_index`."""
        raise AttributeError(
            "presentations have no colour registers -- the sprite's apply to "
            "every drawing of it. To vary colour ACROSS a population, vary "
            "`palette_index`: the register is a row of the tileset's bank, so "
            "different rows are different tables and cost a number rather "
            "than a blend. A fade wants dimmed rows generated once at load."
        )

    @property
    def palette_index(self) -> np.ndarray:
        """`(n,)` int32 view of each one's palette register, for the sheet's
        primary tileset -- the only one an instanced presentation draws."""
        return self._sprite._slots.palette_index[self._span()]

    @contextmanager
    def edit(self):
        """Mark the sprite dirty ONCE around a batch of array writes.

            with swarm.edit():
                swarm.positions[:, 1] += fall
                swarm.visible[dead] = False

        Writing through a view does not go past a setter, so nothing tells the
        sprite it moved. This does, once, rather than per write -- and a frame
        that changes nothing still costs nothing, because `_sync` is gated on
        the same flag.
        """
        try:
            yield self
        finally:
            self._sprite._touch()

    def __len__(self) -> int:
        return int(self._slots.size)

    def __getitem__(self, position: int) -> "Presentation":
        return Presentation(self._sprite, int(self._slots[position]))

    def __iter__(self):
        return (Presentation(self._sprite, int(s)) for s in self._slots)

    def __repr__(self) -> str:
        shape = "contiguous" if self.contiguous else "fragmented"
        return f"<Presentations {len(self)} slots, {shape}>"


class _NoTint:
    """`red`, `green` and `blue`, removed 2026-09-09, kept as refusals.

    Per-sprite RGB multipliers over whatever the palette produced. They went
    for the reason alpha did: **recolouring is a palette job and never a pixel
    one**, and a bank makes every effect they served reachable as a row -- a
    dimmer trail, a hit flash, a costume. Keeping both would have left two
    mechanisms for one intent, and the multiplier was the one that could not
    say what colour it was producing.

    The register survives below them as a whole-PASS tint on `SpriteBatch`,
    driven by nothing, for a post-process over a layer. It is not addressable
    from here and is not per sprite.

    Refusals rather than deletions, on the `_NoAlpha` precedent: the old
    spelling is in shipped examples, and a bare `AttributeError` would be
    accepted by nothing and explain nothing.
    """

    def __init__(self, channel: str) -> None:
        self._channel = channel
        self.__doc__ = f"Gone -- {channel} was a multiplier. See the error."

    def _refuse(self):
        raise AttributeError(
            f"sprites have no {self._channel} register: recolouring is a "
            f"palette job, not a pixel one. A sprite draws through a row of "
            f"its palette bank, so every tint the multipliers served is a "
            f"row -- and a row can say what colour it produces, where a "
            f"multiplier could only darken what was already there.\n"
            f"  to recolour one sprite     `sprite.palette_index[tileset] = row`\n"
            f"  to fade a trail            a dimmer row per ghost\n"
            f"  to build the row           `bank[row] = bank[source]`, then\n"
            f"                             operators, at load"
        )

    def __get__(self, presentation, owner=None):
        if presentation is None:
            return self
        self._refuse()

    def __set__(self, presentation, value) -> None:
        self._refuse()


class _NoAlpha:
    """`alpha`, removed 2026-09-02, kept as a refusal that says where to go.

    Left as a descriptor rather than deleted because `ghost.alpha = 0.4` was
    the documented way to fade a trail, it is in shipped examples, and an
    `AttributeError` on a plain assignment would be silently accepted by
    nothing but would explain nothing either.
    """

    __doc__ = "Gone -- a sprite pixel is drawn or it is not. See the error."

    def _refuse(self):
        raise AttributeError(
            "sprites have no alpha register: a pixel is drawn or it is not, "
            "with nothing in between. The engine paints back to front and "
            "colour information is last-writer-wins, and indexed art carries "
            "that -- index 0 is transparent, everything else opaque, and the "
            "shader discards rather than blends.\n"
            "  to hide a presentation      `visible = False`\n"
            "  to fade a trail             a dimmer row of the palette bank\n"
            "  to make a whole layer sheer `layer.blend = True`"
        )

    def __get__(self, presentation, owner=None):
        if presentation is None:
            return self
        self._refuse()

    def __set__(self, presentation, value) -> None:
        self._refuse()


class Presentation:
    """One drawing of a sprite: where it is, how it is turned, what pose.

        chun[-1].position = (160, 200)
        chun[-1].visible = False

    Minted on access rather than stored, so `chun[-1]` is always a live view
    of slot -1 and never a stale copy. Holding one is holding an index.

    **Slot 0 is reached through the sprite itself.** `chun.position` and
    `chun[0].position` are the same storage, not two values kept in step: the
    sprite's attributes ARE its primary presentation, so a lone sprite reads
    as though presentations do not exist. That is the point -- a fighter's
    author writes `chun.position = (160, 200)` and never learns this class
    is here.
    """

    __slots__ = ("_sprite", "_slot")

    def __init__(self, sprite: "Sprite", slot: int) -> None:
        self._sprite = sprite
        self._slot = sprite._slots.live(int(slot))

    @property
    def slot(self) -> int:
        """This presentation's identity -- its row in every state array.

        Stable for its life. It says nothing about draw order: reordering
        never touches it, and growing capacity never moves it. Where it draws
        is its position in `sprite.before` or `sprite.after`.
        """
        return self._slot

    @property
    def instanced(self) -> bool:
        """True for every presentation but the primary.

        Instanced presentations take three restrictions -- no clip, no
        multipart assembly, and only the sheet's `primary_tileset` -- which is
        what lets four of them and twenty-five thousand share one
        representation.
        """
        return self._slot != PRIMARY

    # -- where it is -------------------------------------------------------

    @property
    def position(self) -> Location:
        """Where this drawing's `origin` lands. **Whole pixels, always.**

        A float is floored on the way IN, so what reads back is what gets
        drawn. See `Sprite.position` for why the engine declines sub-pixel
        positions and where the accumulator belongs instead.
        """
        row = self._sprite._slots.position[self._slot]
        return Location(int(row[0]), int(row[1]))

    @position.setter
    def position(self, value: Union[Tuple[float, float], Location]) -> None:
        self._sprite._slots.position[self._slot] = (
            floor(value[0]),
            floor(value[1]),
        )
        self._sprite._touch()

    @property
    def x(self) -> int:
        """Horizontal position in whole pixels. See `position`."""
        return int(self._sprite._slots.position[self._slot, 0])

    @x.setter
    def x(self, value: float) -> None:
        self._sprite._slots.position[self._slot, 0] = floor(value)
        self._sprite._touch()

    @property
    def y(self) -> int:
        """Vertical position in whole pixels. See `position`."""
        return int(self._sprite._slots.position[self._slot, 1])

    @y.setter
    def y(self, value: float) -> None:
        self._sprite._slots.position[self._slot, 1] = floor(value)
        self._sprite._touch()

    # -- the pose ----------------------------------------------------------

    @property
    def assembly(self) -> Optional[str]:
        """The name of the pose being presented, or None if none is.

        `None` presents nothing, and so does a deliberate blank pose -- six of
        Chun-Li's 3198 assemblies have no parts at all. Presenting nothing is
        a state, not a failure, and a fresh slot starts here.

        **On an instanced slot this is the manual control**, and the only one:
        an instanced presentation never plays a clip. A trail ghost is a
        snapshot driven by hand, frame by frame, out of whatever history the
        game is keeping. Only slot 0 has a cursor that advances itself.

        Two poses an instanced slot will refuse, both because they would break
        the one-texture-one-quad shape the whole population depends on:
        multipart assemblies, and assemblies drawing from a tileset other than
        the sheet's `primary_tileset`.
        """
        index = int(self._sprite._slots.assembly[self._slot])
        if index == NO_ASSEMBLY:
            return None
        return self._sprite._assembly_names[index]

    @assembly.setter
    def assembly(self, key: Union[str, int, Tuple[int, int], None]) -> None:
        sprite = self._sprite
        if key is None:
            if self._slot == PRIMARY:
                sprite._animation = None
            sprite._slots.assembly[self._slot] = NO_ASSEMBLY
            sprite._touch()
            return
        name = sprite._sheet.assembly_name(key)
        if self._slot != PRIMARY:
            self._refuse_unless_instanceable(name, sprite._sheet.assembly(key))
        else:
            # Same state, same rule as `Sprite.assembly`: writing a pose by
            # hand takes the sprite off its clip. Reaching the primary through
            # `sprite[0]` must not be a way round it.
            sprite._animation = None
        sprite._slots.assembly[self._slot] = sprite._assembly_ids[name]
        sprite._touch()

    def _refuse_unless_instanceable(self, name: str, assembly: "Assembly") -> None:
        """The two rules an instanced presentation lives by, checked at the
        write that breaks them rather than at a draw that would have silently
        dropped the slot.

        The rules themselves live on `Sprite`, because the bulk door
        (`Sprite.assembly_id`) has to apply exactly the same ones and two
        copies would be two chances to drift.
        """
        self._sprite._refuse_unless_instanceable(
            name, assembly, f"this is slot {self._slot}"
        )

    # -- how it is turned --------------------------------------------------

    @property
    def orientation(self) -> Orientation:
        """How this drawing is turned -- one of the eight axis-aligned
        orientations, the same vocabulary a tile placement speaks.

        The mirror and the turn happen about the sprite's `origin`, which is
        shared: an origin is a fact about the art, not about one drawing of
        it. See `Sprite.orientation`.
        """
        return Orientation(int(self._sprite._slots.orientation[self._slot]))

    @orientation.setter
    def orientation(self, value: Orientation) -> None:
        self._sprite._slots.orientation[self._slot] = int(Orientation(value))
        self._sprite._touch()

    # -- what it is drawn with ---------------------------------------------

    @property
    def palette_index(self) -> PaletteRegisters:
        """Palette selection: `chun.palette_index["character"] = 3`.

        Slot 0 has one register per tileset the sheet draws, because slot 0
        may present art from any of them. An instanced slot draws only the
        primary tileset, so it has exactly that one register -- naming another
        raises rather than silently doing nothing.
        """
        return PaletteRegisters(self._sprite, self._slot)

    red = _NoTint("red")
    green = _NoTint("green")
    blue = _NoTint("blue")
    alpha = _NoAlpha()

    # -- being seen, and being there ---------------------------------------

    @property
    def visible(self) -> bool:
        """Whether this drawing's quad is sent. Nothing else.

        `visible` and `collide` are two independent bits over one presence --
        see `Sprite.visible` for the four states they make. A freshly
        allocated slot is `False`/`False`: PARKED, kept and cheap, out of both
        passes, because allocating capacity must not put art on screen.

        This is also the liveness bit a dense population spawns and reaps
        through, which is why slots are never removed -- a dead bullet is a
        parked slot waiting to be reused.
        """
        return bool(self._sprite._slots.visible[self._slot])

    @visible.setter
    def visible(self, value: bool) -> None:
        self._sprite._slots.visible[self._slot] = bool(value)
        self._sprite._dirty = True

    @property
    def collide(self) -> bool:
        """Whether this presence stamps the layer's collision planes.

        Independent of `visible`, so a cloaked presentation still hits and a
        ghost still does not. A trail's ghosts set this `False`: they are
        afterimages, and an afterimage that could be hit would be a bug the
        art tells you nothing about.

        WHICH named planes a pose stamps is slot 0's -- authored geometry,
        per identity. This bit is every slot's, because a dense population
        needs to spawn and reap.
        """
        return bool(self._sprite._slots.collide[self._slot])

    @collide.setter
    def collide(self, value: bool) -> None:
        self._sprite._slots.collide[self._slot] = bool(value)
        self._sprite._collision_dirty = True

    def __eq__(self, other: object) -> bool:
        """Two handles on the same slot of the same sprite are the same
        presentation -- they are views, not values."""
        if not isinstance(other, Presentation):
            return NotImplemented
        return other._sprite is self._sprite and other._slot == self._slot

    def __hash__(self) -> int:
        return hash((id(self._sprite), self._slot))

    def remove(self) -> None:
        """Take this presentation out of the draw order and free its slot.

        Finds which chain holds it. See `Chain.remove` for why this is not how
        a swarm reaps its dead.
        """
        sprite = self._sprite
        for chain in (sprite.before, sprite.after):
            if self._slot in chain._order:
                chain.remove(self)
                return
        raise ValueError(
            f"slot {self._slot} is in neither chain -- the primary cannot be "
            f"removed, and nothing else is drawn without being in one"
        )

    def __repr__(self) -> str:
        pose = self.assembly or "nothing"
        where = "primary" if self._slot == PRIMARY else f"slot {self._slot}"
        return f"<Presentation {where} {pose!r} at ({self.x}, {self.y})>"
