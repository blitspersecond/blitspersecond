from time import perf_counter
from typing import TYPE_CHECKING, List, Optional

from pyglet.gl import (
    GL_BLEND,
    GL_ONE_MINUS_SRC_ALPHA,
    GL_SRC_ALPHA,
    glBlendFunc,
    glDisable,
    glEnable,
)

from .layer import Layer

if TYPE_CHECKING:
    from blitspersecond.internal.framebuffer import Framebuffer


class Layers:
    """Ordered collection of layers, background (index 0) to foreground.

        background = TileLayer("tiles/bg.png", 8, 8)
        actors = SpriteLayer("band.png", 12, 12)
        bps.layers.add(background)
        bps.layers.add(actors)
        actors.after(background)

    Owns the ordering. Layers reposition themselves through their fluent
    interface (layer.before/after/first/last/swap_with), which delegates to the
    private `_move_*` helpers here -- the collection is the single source of
    truth, and a bound layer holds a back-reference to it (set on add, cleared
    on remove).

    An engine builds a layer; this collection places it. A layer is
    off-screen until `add` gives it somewhere to be, and off-screen again
    after `remove` -- those two verbs are the whole of what this owns.

    **`Layers` imports no layer class**, so adding a fourth kind of layer
    needs no line here -- and there is no import cycle through `graphics` to
    defer around. Keep it that way: building belongs to the thing being built.

    The design record is `bps/reference/layers.md`.
    """

    def __init__(self):
        self._layers: List[Layer] = []
        self._background: tuple[float, float, float] = (0.0, 0.0, 0.0)
        self._commit_duration: float = 0.0

    def add(self, layer: Layer) -> None:
        """Place a layer at the foreground of the draw order, and so on screen.

        A layer arrives here unattached -- built by its engine, drawing
        nowhere -- and this is what gives it a place. Adding is the only way
        one gets on screen, and it is the whole of what `add` does: it does
        not build, and it does not move.

        Adding a layer that is already in this order RAISES rather than
        moving it to the front: placing and repositioning are different
        questions, and `first()`/`last()`/`before()`/`after()` are the second
        one.

        A layer sits in one draw order at a time; `remove()` it from the
        other first."""
        if layer._layers is self:
            raise ValueError(
                "layer is already in this draw order -- add() places a "
                "layer, it does not move one; reposition it with "
                "first()/last()/before()/after()"
            )
        if layer.attached:
            raise ValueError(
                "layer is already in another Layers collection; remove() "
                "it there first -- one layer cannot sit in two draw orders"
            )
        self._layers.append(layer)
        layer._bind(self)

    def remove(self, layer: Layer) -> None:
        """Take a layer out of the draw order, leaving it built but off-screen.

        The layer survives -- its contents, its tag, everything but its
        place -- so `add` can put it back later. It goes back at the
        foreground, not where it was; a draw order does not keep a space
        open for something that left it.

        Raises if the layer isn't in this collection: removing something
        that was never here, or has already been taken out, is a bug in the
        caller and not a quiet no-op to paper over."""
        if not isinstance(layer, Layer):
            raise TypeError(f"expected a Layer; got {type(layer)}")
        if layer._layers is not self:
            raise ValueError(f"{layer!r} is not in this Layers collection")
        self._layers.remove(layer)
        layer._bind(None)

    # -- the frame: prepare, then commit ---------------------------------
    #
    # The engine calls these two either side of the game's tick, and names no
    # per-layer step itself:
    #
    #     layers.prepare()      # BEGIN
    #     game_loop()           # the game mutates
    #     layers.commit(...)    # COMMIT, and the tidy that follows it
    #
    # This collection is the compositor. It used to be `Display._compose`,
    # which meant a window object read `Layer.enabled` and `Layer.blend` --
    # state the collection owns and the display had no business knowing. The
    # display now only hands over the buffer to draw into.

    @property
    def background(self) -> tuple[float, float, float]:
        """The colour the frame clears to before any layer draws."""
        return self._background

    @background.setter
    def background(self, colour: tuple[float, float, float]) -> None:
        self._background = colour

    @property
    def commit_duration(self) -> float:
        """Seconds the last commit took, excluding the present path.

        Billed at game rate: one commit per tick, whatever the panel does.
        Note this now INCLUDES `prepare`'s work, which previously fell between
        `gameloop_ms` and the old `compose_duration` and was in neither.
        """
        return self._commit_duration

    def prepare(self) -> None:
        """Bring every attached layer up to date for the tick about to run.

        **Every ATTACHED layer, not every enabled one.** Attachment is the
        statement of participation: a layer you do not want processed is one
        you do not add. `enabled` therefore means exactly one thing -- whether
        the layer is drawn -- and a hidden layer keeps advancing its clips and
        keeps repainting a typed line.

        Called BEFORE the game's tick, so the game reads a world already
        current: a sprite's pose and a console's echoed line are this tick's
        when the game looks at them, the same guarantee the input cut gives.

        A layer with nothing to do costs one `is None` test, so a scene that
        animates nothing and types nothing pays nothing.
        """
        for layer in self._layers:
            layer.prepare()

    def commit(self, fbo: "Framebuffer") -> None:
        """Draw the frame the tick produced into `fbo`.

        Writes the buffer it is handed and nothing else. It does not flag the
        frame finished, does not know a second buffer exists, and takes no
        part in showing this one -- swapping, promoting and presenting are
        the display's, and so is deciding when a written frame counts as
        done.

        An empty draw order costs exactly the clear, so a game that has added
        no layers costs one clear and a swap. Deliberate rather than
        incidental: the frame-pacing numbers in
        `docs/bps/DECOUPLED_PRESENTATION.md` were all measured against a
        clear-and-swap, and stay comparable only while the no-layer case
        still IS one.
        """
        started = perf_counter()
        fbo.clear(self._background)
        if self._layers:
            with fbo.as_target():
                self._draw()
        self._commit_duration = perf_counter() - started

    def _draw(self) -> None:
        """Every layer, background to foreground, blend tracked.

        EVERY layer, not every enabled one. The walk calls `composite` on all
        of them and reads none of their state; a layer that is not being
        drawn returns without drawing. What `enabled` means belongs to the
        layer, the same way what a tick means belongs to it -- a walk that
        interpreted the flag would be a second place that knows what it
        implies.

        Blend is COMPOSITING state and is READ here, per layer, from
        `Layer.blend`. It used to be established once around the whole loop,
        because every pillar wanted the identical two calls. That stopped
        being right when indexed art became strictly drawn-or-discarded: the
        engine paints back to front with no depth test and no resolve, so
        colour is last-writer-wins and `indexed.frag` discards index 0 rather
        than blending it away. A sprite layer over a tilemap composes
        correctly with blending OFF -- transparent texels never reach here.

        The func is set unconditionally: it is inert while blend is off, and
        setting it once beats tracking whether it still holds. The present
        pass disables blend again for its own quad, which is why even this
        much is re-established per frame rather than at startup.
        """
        glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
        blending = False
        for layer in self._layers:
            if layer.blend != blending:
                blending = layer.blend
                (glEnable if blending else glDisable)(GL_BLEND)
            layer.composite()  # decides whether it draws; issues its own draw
        if blending:
            glDisable(GL_BLEND)

    def find(self, *, tag: str) -> Optional[List[Layer]]:
        """Bound layers carrying `tag`, in stack order -- or None if none do.

        Unlike a plain lookup returning empty, None distinguishes "nothing
        has this tag" from a caller who forgot to check before indexing."""
        matches = [layer for layer in self._layers if layer.tag == tag]
        return matches if matches else None

    # -- reposition (all operate on already-bound layers) ----------------

    def __setitem__(self, index: int, layer: Layer) -> None:
        """Reposition an already-bound layer to `index`. This does NOT replace
        the layer currently at `index` -- it moves `layer` there and shuffles
        the rest along."""
        if not isinstance(layer, Layer):
            raise TypeError("Can only reposition Layer objects")
        if layer._layers is not self:
            raise ValueError("Can only reposition layers already in the collection")
        if not 0 <= index < len(self._layers):
            raise IndexError(
                f"index {index} out of bounds for {len(self._layers)} layers"
            )
        # Remove first, then insert at the target: because the list is one
        # shorter after removal, inserting at `index` lands the layer exactly
        # at `index` (no off-by-one regardless of its original position).
        self._layers.remove(layer)
        self._layers.insert(index, layer)

    def _target(self, other: object) -> Layer:
        """Check that `other` is a layer in THIS order, and hand it back.

        RAISES rather than returning None. A silently ignored reorder leaves
        a frame that is merely subtly wrong -- nothing reports it, and the
        search starts at the drawing rather than at the ordering. A reorder
        that cannot happen must say so.
        """
        if not isinstance(other, Layer):
            raise TypeError(f"expected a Layer; got {type(other)}")
        if other._layers is not self:
            raise ValueError(
                "cannot order against a layer that is not in this draw "
                "order -- layers.add() it first"
            )
        return other

    def _move_before(self, layer: Layer, other: object) -> None:
        """Place `layer` immediately behind `other` (drawn just before it)."""
        target = self._target(other)
        if layer is target:
            return
        self._layers.remove(layer)
        self._layers.insert(self._layers.index(target), layer)

    def _move_after(self, layer: Layer, other: object) -> None:
        """Place `layer` immediately in front of `other` (drawn just after it)."""
        target = self._target(other)
        if layer is target:
            return
        self._layers.remove(layer)
        self._layers.insert(self._layers.index(target) + 1, layer)

    def _swap(self, a: Layer, b: object) -> None:
        target = self._target(b)
        if a is target:
            return
        i, j = self._layers.index(a), self._layers.index(target)
        self._layers[i], self._layers[j] = self._layers[j], self._layers[i]

    def _to_back(self, layer: Layer) -> None:
        """Move to the background (index 0, drawn first)."""
        self._layers.remove(layer)
        self._layers.insert(0, layer)

    def _to_front(self, layer: Layer) -> None:
        """Move to the foreground (drawn last)."""
        self._layers.remove(layer)
        self._layers.append(layer)

    def __getitem__(self, index: int) -> Layer:
        return self._layers[index]

    def __iter__(self):
        """Iterate layers from background to foreground."""
        return iter(self._layers)

    def __len__(self) -> int:
        return len(self._layers)
