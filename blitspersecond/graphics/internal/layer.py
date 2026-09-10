from typing import FrozenSet, Hashable, Optional, Tuple
from uuid import uuid4

import numpy as np

from ..collision import Collider, CollisionQuery, as_collider


class Layer:
    """A place in the compositor's draw order -- and, in every subclass, the
    content that place shows. A pillar IS its layer: `Console` is one and
    `TileMap` will be, so building one builds both the thing you draw with
    and where it sits. The base class owns the compositing concerns --
    enabled, tag, blend, stack position; opacity/offset later -- and the
    collision question, which is the one thing here that is not about
    drawing.

    **Collision is on the base because the answer must not depend on which
    pillar asked.** *A layer is one texture, one draw call, and one
    answerable collision identity*, so `collides_with` means the same thing
    between two tile layers, a sprite and a console, or either against the
    other, and there is one statement of it rather than one per pillar that
    can drift. What a subclass supplies is the geometry -- `collidable`,
    `planes` and `collision_plane` below -- and the bare base supplies
    none, so an ordinary layer refuses the question loudly instead of
    answering False about a mask it never had.

    You construct a pillar yourself and `bps.layers.add(layer)` places it.
    Until it is added it is unattached and off-screen, and reordering an
    unattached layer raises, because a draw order it isn't in cannot be
    changed. Once it is in, reorder fluently: `layer.after(hud)`,
    `layer.first()`, `a.swap_with(b)`.
    """

    def __init__(self, tag: Optional[str] = None) -> None:
        self._id: str = str(uuid4())
        self._enabled: bool = True
        self._collidable: bool = False
        self._blend: bool = False
        self._tag: Optional[str] = tag
        self._layers = None  # back-reference, set by Layers.add

    @property
    def id(self) -> str:
        """Stable unique id -- handy for lookup / stable sort keys."""
        return self._id

    @property
    def enabled(self) -> bool:
        """Whether the compositor draws this layer. Drawing only: a disabled
        layer keeps its contents and keeps answering `collides_with`, in the
        same way a sprite's `visible` is separate from its `collide`. Turning
        a layer off is a view decision, not a world one -- to take its
        contents out of play, clear their collide bits."""
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        if not isinstance(value, bool):
            raise ValueError("enabled must be a boolean")
        self._enabled = value

    @property
    def blend(self) -> bool:
        """Whether this layer's fragments blend with what is already drawn.

        **Off by default, and that is the engine's model rather than a
        performance default.** Indexed art is drawn or discarded -- index 0 is
        transparent and `indexed.frag` discards it -- so a sprite layer over a
        tilemap composes correctly with blending off.

        Translucency is a per-LAYER effect and this is where it lives: a
        see-through overlay, an additive flash, a fog sheet. Not a way to fade
        one sprite among many; that is a palette row. See
        `bps/reference/graphics/layers.md`.
        """
        return self._blend

    @blend.setter
    def blend(self, value: bool) -> None:
        if not isinstance(value, bool):
            raise ValueError("blend must be a boolean")
        self._blend = value

    @property
    def tag(self) -> Optional[str]:
        return self._tag

    @tag.setter
    def tag(self, value: Optional[str]) -> None:
        self._tag = value

    @property
    def attached(self) -> bool:
        """Whether this layer is in a draw order, and so on screen.

        A bool, deliberately: a layer reports whether it has a place and
        cannot hand out the order it sits in. Reordering THIS layer is what
        `before/after/first/last/swap_with` below are for.

        The one thing a layer knows that its collection does not tell you for
        free -- a layer handed to a helper cannot ask `bps.layers` without
        also being handed the engine."""
        return self._layers is not None

    def prepare(self) -> None:
        """Bring this layer up to date for the tick about to run. A no-op
        unless the layer has something to do.

        The BEGIN slot, called by `Layers.prepare` before the game's tick.
        Sprites move a clip on; a console repaints the line being echoed;
        tiles have neither.

        **One slot, and the walk knows nothing else.** Whatever a pillar
        calls its own per-tick work -- `_update_animations`, `_repaint_input`
        -- the compositor reaches it only through this method. Probing for
        pillar-specific names would make those names an interface without
        declaring one, discoverable only by reading the walk. A real method
        rather than a `getattr` probe for the same reason: it is typed and
        greppable and cannot be satisfied by accident.
        """

    def _bind(self, layers) -> None:
        """Record which collection holds this layer. Called by Layers.add and
        Layers.remove ONLY -- binding is the collection's to decide, and a
        public setter let a layer be told it was in an order that had never
        heard of it."""
        self._layers = layers

    # -- fluent ordering (delegates to the bound collection) -------------

    def _bound(self):
        """The collection this layer sits in, or raise saying it sits in none.

        There is no sensible quiet answer: a position in an order you are not
        in is undefined, so asking is an error rather than a no-op."""
        if self._layers is None:
            raise RuntimeError(
                f"{type(self).__name__} is not in a draw order, so it cannot "
                f"be reordered -- place it with bps.layers.add(layer) first"
            )
        return self._layers

    def before(self, other: object) -> "Layer":
        """Draw just before `other` (one step toward the background).

        `other` is any layer already in this draw order. A pillar IS a layer,
        so you name it directly -- `hud.before(console)`, not its contents."""
        self._bound()._move_before(self, other)
        return self

    def after(self, other: object) -> "Layer":
        """Draw just after `other` (one step toward the foreground).

        `other` is any layer already in this draw order."""
        self._bound()._move_after(self, other)
        return self

    def swap_with(self, other: object) -> "Layer":
        """Swap stack positions with `other`."""
        self._bound()._swap(self, other)
        return self

    def first(self) -> "Layer":
        """Move to the background (drawn first)."""
        self._bound()._to_back(self)
        return self

    def last(self) -> "Layer":
        """Move to the foreground (drawn last)."""
        self._bound()._to_front(self)
        return self

    # -- collision (the shared question; the geometry is a subclass's) -----

    def _collision_refusal(self) -> Optional[str]:
        """Why this layer may not be made collidable, or None if it may be.

        The subclass hook behind the `collidable` setter, so the rule that
        decides it is stated once per pillar rather than once per place that
        checks. The bare base refuses, because a layer with no masks behind
        it answering collision questions is the silent bug the whole gate
        exists to prevent.
        """
        return f"{type(self).__name__} keeps no collision masks"

    def _require_collidable(self) -> None:
        """Raise unless this layer is taking part in collision right now.

        **The one statement of the gate**, called both where a handle is
        minted and where a question is asked, so the two doors cannot come
        to mean different things. Nothing here answers False: a layer that
        is out of collision has no answer to give, and inventing one would
        hide a game asking about geometry it switched off -- which is the
        bug that looks exactly like working code until a level ships with
        its hazards inert.

        The two refusals read differently because the fixes differ. Art that
        can never collide wants authoring differently; a switch you turned
        off wants turning back on.
        """
        if self._collidable:
            return
        refusal = self._collision_refusal()
        if refusal is not None:
            raise ValueError(
                f"{self} cannot take part in a collision question: {refusal}."
            )
        raise ValueError(
            f"{self} has collidable=False, so it stamps nothing and a "
            f"question involving it has no answer. Set collidable=True to "
            f"take part again."
        )

    @property
    def collidable(self) -> bool:
        """Whether this layer takes part in collision at all -- **a switch**,
        and the cheapest one there is.

            hazards.collidable = False      # the whole layer, off

        Turning it off stops the layer stamping -- on a sprite, the
        per-frame recomposition of every plane it owns -- and makes every
        question involving it RAISE. Not answer False: a layer that is out of
        collision has no answer to give, and a False would be indistinguishable
        from "these two do not touch", which is how a level ships with its
        hazards silently inert. The switch is therefore read at the question
        as well as at the stamp, and neither reads stale bits, because the
        planes a switched-off layer stopped maintaining are never consulted.

        **Not every layer may be switched on.** Solidity is keyed on palette
        index 0 meaning transparent, so a layer whose art is not palettised
        has nothing to derive a mask from -- collision there is unbuildable
        rather than off, and setting this True raises saying so. Both sides
        of a question must be collidable, which is therefore one rule and not
        two: everything that collides is palettised.
        """
        return self._collidable

    @collidable.setter
    def collidable(self, value: bool) -> None:
        if not isinstance(value, bool):
            raise ValueError("collidable must be a boolean")
        if value:
            refusal = self._collision_refusal()
            if refusal is not None:
                raise ValueError(refusal)
        self._collidable = value

    @property
    def planes(self) -> FrozenSet[Hashable]:
        """The named collision planes this layer's art authored.

        Empty on everything that keeps a single mask -- tiles -- which is not
        a degenerate case but the common one. The vocabulary is opaque to the
        engine: keys come from the art and mean whatever the game decides.

        **Opaque includes the type.** A sprite sheet authors strings --
        `attack`, `body_hurt` -- and a console authors the four glyph plane
        numbers, because a font has no names to give. So a key is anything
        hashable, and the engine reserves no spelling of one.
        """
        return frozenset()

    @property
    def collision_offset(self) -> Tuple[int, int]:
        """Where screen `(0, 0)` sits inside this layer's collision planes.

        `(0, 0)` for anything whose planes ARE the screen -- a sprite is at a
        coordinate and a console is screen space, so neither has anywhere else
        to be. A tilemap has a camera over a world larger than the screen, so
        its planes are stored with a border and it reports that border plus
        whatever sub-word remainder its camera has not moved.

        **One number carries both**, because they are the same kind of thing:
        a constant displacement between where a bit is stored and where it
        appears. Splitting them would mean two things to keep in step.

        It exists so the mask never has to move for fine scrolling. The
        compiled query reads each plane through its own offset for nothing
        (measured 2026-09-07: 0.469us against 0.481us at zero, inside noise),
        where shifting the plane instead would cost tens of microseconds per
        layer per frame.
        """
        return (0, 0)

    def collision_plane(self, name: Optional[Hashable] = None) -> np.ndarray:
        """One packed screen-space plane: `None` for the default silhouette,
        or a name from `planes`.

        The subclass hook. Everything above reaches geometry through here,
        so a pillar implements one method to join the protocol, and the
        gates that decide whether a call is even legal have already run in
        `collider()`.
        """
        raise NotImplementedError(
            f"{type(self).__name__} keeps no collision mask"
        )

    def collider(self, name: Optional[Hashable] = None) -> Collider:
        """A handle on this layer's geometry -- the default plane, or one
        named plane of it.

            wall = terrain.collider()
            fist = chunli.collider("attack")

        **Both gates fire here**, in the call that named the thing, rather
        than inside an `.at()` further down the frame. A layer not taking
        part raises -- one answering False forever is a silent bug that looks
        like working code -- and so does an unknown plane name, because a
        typo'd hitbox that never hits is the same bug wearing a costume. The
        two refusals read differently on purpose: art that can never collide
        wants fixing, and a switch you turned off wants turning back on.

        Handles are worth keeping. One resolves its plane on every read and
        caches nothing, so a handle made at setup keeps answering correctly
        as the world moves underneath it.
        """
        self._require_collidable()
        if name is None:
            return Collider(self, None)
        vocabulary = self.planes
        if name not in vocabulary:
            if not vocabulary:
                raise KeyError(
                    f"{name!r}: this layer authors no named collision planes, "
                    f"so its silhouette -- collider() with no name -- is all "
                    f"there is to select."
                )
            raise KeyError(
                f"{name!r}: unknown collision plane. This layer authors "
                f"{sorted(vocabulary)}."
            )
        return Collider(self, name)

    def collides_with(self, other) -> CollisionQuery:
        """Prepare a collision question against another layer or collider.

            if player.collides_with(ladders).at(x, y, w, h):

        Sugar for `self.collider().collides_with(other)` -- a bare layer on
        either side means its default plane, so a game that never names one
        never meets `Collider`. Nothing is computed until `.at()` runs.

        **`enabled` is not consulted.** A layer turned off keeps answering,
        the same split a sprite makes one level down between `visible` and
        `collide`: what is drawn is a view decision and what is solid is a
        world one. To take a layer's contents out of play, clear their
        collide bits rather than hiding the layer.
        """
        return CollisionQuery(self.collider(), as_collider(other))

    def composite(self) -> None:
        """The COMMIT slot: draw this layer, if it is being drawn.

        The compositor calls this on every layer in the order and reads none
        of their state -- what `enabled` MEANS is decided here, by the layer
        it belongs to, exactly as `prepare` decides what a tick means to it.
        A walk that interpreted layer state would be a second place that
        knows what a flag implies, and the two would drift.

        `enabled` is a view decision and only that: a layer turned off draws
        nothing and keeps everything else, still advancing on `prepare` and
        still answering collision questions.

        **So a commit has two halves and only one of them is gated.**
        `_settle_collision` runs every frame for every attached layer and
        brings the world half current -- masks, and whatever a pillar has to
        move to keep them honest. `_composite` draws, and is the only thing
        `enabled` decides.

        Unconditional rather than gated on `collidable` as well, which would
        save a little on a layer that is neither drawn nor solid: a layer must
        behave the same whether or not it is visible, and the only difference
        allowed is that its pixels do not reach the framebuffer. Making
        hidden-and-uncollidable a third case would mean a layer whose camera
        froze while it was hidden and caught up when it came back -- two
        behaviours where the point is that there is one.

        The case that forces the split is a collider layer nobody sees -- the
        ladders a map author paints for the engine rather than the player. It
        scrolls with everything else, so its plane has to keep moving while
        nothing draws it.
        """
        self._settle_collision()
        if self._enabled:
            self._composite()

    def _settle_collision(self) -> None:
        """Bring this layer's collision geometry up to date for the commit.

        A no-op unless the layer keeps a mask. This is the ONLY road to the
        work: each pillar split it out of the sync at the top of its own
        `_composite`, so the draw path no longer does it and a layer settles
        exactly once whether or not it is drawn. The split is what lets a
        layer nobody sees keep a plane worth asking about.

        It is not `prepare`. That runs before the game's tick and brings a
        layer's presentation current for the tick about to run; this runs at
        the commit and settles what the NEXT tick will ask about, which is the
        frame just painted -- or, here, the frame that would have been.
        """

    def _composite(self) -> None:
        """Draw this layer's content into the bound framebuffer.

        The subclass hook, reached through `composite` above. Subclasses ARE
        the content and override this; the bare base draws nothing."""

    def __str__(self) -> str:
        return f"id: {self._id} [{self._tag}]"
