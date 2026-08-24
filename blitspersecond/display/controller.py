"""The shared surface of a thing that CONTROLS a layer without being one.

Console and TileMap are both this: an object holding a layer, adding semantics
the layer has no business knowing about (a cursor, a camera), and reaching
through for the things that belong to the layer's PLACE in the draw order.

# ---------------------------------------------------------------------------
# API DECISIONS (2026-08-24, branch `inversion`)
#
# 1. DECISION: a controller is NOT a Layer. It HAS one, exposed as `.layer`,
#    and it PROXIES the draw-order verbs to it. `Console`, not `ConsoleLayer`.
#    REQUIREMENT: the semantics a controller adds (teletype, streaming camera)
#    must stay optional -- the same glyph grid and the same tile layer have to
#    be drivable WITHOUT them, because a game wanting text and a game wanting
#    the raw grid are two audiences with one surface each.
#    EVIDENCE: fusing them was tried and lasted A DAY -- Console was made
#    Layerable 2026-07-21 and demoted 2026-07-22, and that demotion stands.
#    Inheriting Layer would also put a controller in the compositor's draw
#    list directly, which is what `.layer` exists to avoid.
#
# 2. DECISION: the proxies return the CONTROLLER, not the layer, so ordering
#    chains in the vocabulary you started in:
#        console.before(sprites).write("ready")
#    REQUIREMENT: a caller holding a controller should never have to drop to
#    `.layer` and back for an ordinary operation. Reaching for `.layer` is for
#    advanced work, not for saying where something draws.
#    EVIDENCE: observed. `layers.add` returning the layer meant
#    `bg.layer.before(fg.layer)` was the only spelling, and the natural
#    `bg.before(fg)` raised AttributeError -- while the inline idiom
#    `console = layers.add(Console(...))` silently DISCARDED the console
#    (verified collected, with no back-reference from the layer). Giving
#    controllers the verbs removes the reason to want add's return value at
#    all, so the two-line construct-then-add form is the only one anyone
#    writes.
#
# 3. DECISION: `tag` and `attached` are reach-throughs holding NO state of
#    their own; the layer is the single source of truth.
#    REQUIREMENT: identical syntax must mean the same thing whichever
#    accessor it was reached through -- the load-bearing rule from the
#    Console block. A controller that cached `tag` would answer differently
#    from its own layer after `layer.tag = ...`.
# ---------------------------------------------------------------------------
"""

from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from .layer import Layer


class Controller:
    """Mixin for an object that owns a layer and speaks for it.

    Subclasses provide `layer`. Everything here is a reach-through -- no
    duplicate state, no second source of truth.
    """

    @property
    def layer(self) -> "Layer":
        """The layer this controller drives. Subclasses provide it.

        The name is deliberately generic and deliberately shared: it is the
        hook `layers.add`/`remove` use to find the layer inside a wrapper
        without knowing what kind of wrapper it is, so a caller holding a
        mixed bag needs no idea which one it has.
        """
        raise NotImplementedError

    # -- the layer's place in the draw order -------------------------------

    @property
    def attached(self) -> bool:
        """Whether this controller's layer is in a draw order, and so on
        screen. Read-through -- a controller carries no draw-order state."""
        return self.layer.attached

    @property
    def tag(self) -> Optional[str]:
        """The layer's tag. `tag` is a draw-order/lookup concept and the
        layer is the thing actually sitting in one, so this reaches through
        rather than the controller carrying its own."""
        return self.layer.tag

    @tag.setter
    def tag(self, value: Optional[str]) -> None:
        self.layer.tag = value

    def before(self, other: object) -> "Controller":
        """Draw just before `other` (one step toward the background).

        `other` may be a layer or another controller -- whatever you can
        `layers.add`, you can order against."""
        self.layer.before(other)
        return self

    def after(self, other: object) -> "Controller":
        """Draw just after `other` (one step toward the foreground)."""
        self.layer.after(other)
        return self

    def swap_with(self, other: object) -> "Controller":
        """Trade places in the draw order with `other`."""
        self.layer.swap_with(other)
        return self

    def first(self) -> "Controller":
        """Move to the background -- drawn first, behind everything."""
        self.layer.first()
        return self

    def last(self) -> "Controller":
        """Move to the foreground -- drawn last, in front of everything."""
        self.layer.last()
        return self
