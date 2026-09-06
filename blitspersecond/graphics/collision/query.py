"""`CollisionQuery`: one prepared pairwise question, and the windowed AND."""

from typing import Optional

from blitspersecond.common import SCREEN

from .kernel import collide_any


class CollisionQuery:
    """A prepared question between two colliders. Ask it with `.at()`.

        query = player.collides_with(ladders)   # prepared, computes nothing
        query.at(x, y, w, h)                    # -> bool, screen-space box
        query.at()                              # -> bool, whole screen

    **The answer is a bool and stays a bool.** Detail is expressed by asking
    a narrower question -- a smaller window, a named plane on either side --
    not by a richer answer. What a game does about an overlap is a game's
    decision, and an engine returning a contact point or a penetration depth
    would be an engine with an opinion about physics it does not have. At
    ~7us a box, a fighting-game roster of boxes costs ~100us a frame, so the
    way to learn more is to ask again.

    **Screen space, and that is a semantic and not an implementation
    detail.** "Collides" means *visually coincident on screen*: two layers
    scrolling at different parallax rates collide where they APPEAR to
    overlap, which is what a player sees and therefore what a player will
    expect. Coordinates given to `.at()` are screen pixels, (0, 0) top-left.

    **It holds the two colliders, never their mask arrays.** Both sides
    resolve at each `.at()`, because the arrays underneath do not survive a
    frame: a tile layer swaps mask buffers when it scrolls, and a sprite
    layer recomposes its planes from nothing every frame. A query is
    therefore worth keeping -- prepare it once at setup and ask it every
    tick.

    **A question about a layer that is out of collision raises rather
    than answering False.** A False there would be indistinguishable from
    "these two do not touch", so a layer switched off -- or one that could
    never collide -- would read as working code forever. The gate is
    `Layer._require_collidable`, and it fires here as well as where the
    handle was minted, because the switch can move in between.

    **Draw state is not consulted, on purpose.** A disabled layer keeps
    answering, and an invisible presentation keeps stamping unless its own
    `collide` bit says otherwise. Turning a layer off is a view decision;
    taking its contents out of play is a world decision, and conflating them
    would make every fade, blink and cutscene a gameplay event.
    """

    __slots__ = ("_a", "_b")

    def __init__(self, a, b) -> None:
        self._a = a
        self._b = b

    @property
    def colliders(self):
        """The two participants, in the order the question was asked."""
        return self._a, self._b

    def at(
        self,
        x: Optional[int] = None,
        y: int = 0,
        w: int = 0,
        h: int = 0,
    ) -> bool:
        """True if the two planes share any solid pixel inside the
        screen-space box `(x, y, w, h)`; `.at()` with no arguments asks the
        whole screen.

        Half-open in both axes, like every rectangle in the engine: a box
        ending at x=128 does not include column 128. The box is clipped to
        the screen rather than refused, so a hitbox that has walked off the
        edge answers False instead of raising -- a thing off-screen has not
        stopped existing, it has stopped being able to touch anything.
        """
        # The switch is read here and not only where stamping happens: a
        # layer turned off between preparing this question and asking it
        # stopped maintaining its planes, so what is left in them describes a
        # frame that has gone. Two attribute reads on the common path; the
        # refusal itself is Layer's, so both doors say the same thing.
        if not (self._a.layer.collidable and self._b.layer.collidable):
            self._a.layer._require_collidable()
            self._b.layer._require_collidable()
        ma = self._a.collision_mask
        mb = self._b.collision_mask
        ax, ay = self._a.offset
        bx, by = self._b.offset
        if x is None:
            x0, y0, x1, y1 = 0, 0, SCREEN.width, SCREEN.height
        else:
            # Clipped to the screen rather than refused: a hitbox that has
            # walked off the edge has stopped being able to touch anything,
            # not stopped existing.
            #
            # The kernel reads out-of-range as empty, so this is not what
            # makes an off-screen box safe -- it is what stops a preposterous
            # one being slow. Without it `at(-1_000_000, 0, 2_000_000, 10)`
            # walks 31,250 empty chunks to reach the same False.
            x0, y0 = max(int(x), 0), max(int(y), 0)
            x1 = min(int(x) + int(w), SCREEN.width)
            y1 = min(int(y) + int(h), SCREEN.height)
            if x0 >= x1 or y0 >= y1:
                return False
        return bool(collide_any(ma, ax, ay, mb, bx, by, x0, x1, y0, y1))

    def __repr__(self) -> str:
        return f"<CollisionQuery {self._a!r} x {self._b!r}>"
