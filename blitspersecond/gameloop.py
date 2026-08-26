"""The game loop a game writes: subclass this, fill in ``run``.

``GameLoop`` is the taught shape. A bare function works too -- ``bps.loop``
takes either -- but a function has nowhere to keep anything, so the moment a
game needs state it either reaches for globals or ends up here. Starting
people on the class avoids teaching a habit and then unteaching it.

The engine deliberately does NOT import this module. ``bps.loop`` reduces
whatever it is handed to a plain zero-argument callable by duck-typing, so
the sugar depends on the engine and never the other way round.
"""

from abc import ABC, abstractmethod

from blitspersecond.blitspersecond import BlitsPerSecond


class GameLoop(ABC):
    """One scene's fixed step, plus somewhere to keep its state.

        class Title(GameLoop):
            def __init__(self):
                self.frames = 0

            def run(self):
                self.frames += 1
                if self.frames > 60:
                    self.bps.loop = Game

        BlitsPerSecond().start(Title)

    ``run`` is called once per fixed step -- sixty times a second -- not once.
    It takes no arguments: the engine is ``self.bps``, and anything else the
    scene needs is an ordinary attribute set in an ordinary ``__init__``.
    There is no base ``__init__`` to chain to, deliberately -- a subclass
    never has to remember ``super()``, and forgetting it cannot half-build a
    scene.

    **Change scene once, not every frame.** The ``if`` above is the part that
    matters. ``run`` is called sixty times a second, so a line like

        def run(self):
            self.bps.loop = Game          # WRONG -- no condition

    does not mean "go to Game", it means "go to Game, sixty times a second".
    Each of those builds a new ``Game`` and runs its ``__init__`` again, so
    whatever the scene sets up on the way in -- a level, a spritesheet, a
    piece of music -- is set up again, sixty times a second. Put the change
    behind something that becomes true once: a key press, a timer, a finished
    animation.

    The engine takes you at your word and does not deduplicate this. Handing
    over ``Game`` and handing over ``Game()`` are the same thing -- the first
    is built for you at assignment, the second you built yourself -- and
    neither is cached or reused. That is deliberate: a game rebuilding a
    scene every frame should feel like it, because that is how you notice.

    Two things this shape does not do yet, both parked as "Give scenes a
    registry and shared state": a scene cannot be handed anything on the way
    in (``bps.loop = Game`` builds ``Game()`` with no arguments), and there is
    nowhere for state that should outlive a single scene.
    """

    @property
    def bps(self) -> BlitsPerSecond:
        """The engine.

        Resolved per access rather than held, because ``BlitsPerSecond()`` is
        an idempotent "engine exists" barrier that hands back the one engine
        -- so this costs a dict lookup and needs no constructor to set it up.

        Notably it is NOT a class attribute. A bare ``_bps = BlitsPerSecond()``
        in the class body is evaluated at class-definition time, so importing
        this module would boot the display and block through warm-up -- and
        since the package exports GameLoop, plain ``import blitspersecond``
        would open a window before the program had said anything at all.
        """
        return BlitsPerSecond()

    @abstractmethod
    def run(self) -> None:
        """One fixed step. Move things, draw things."""
        raise NotImplementedError
