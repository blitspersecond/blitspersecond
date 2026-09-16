"""The boundary every device settles on, together.

One object holds the ingresses and cuts all of them from a single call, which
is what makes per-device storage safe: the rings are separate, the numbering
is not. Frame N means the same interval on the keyboard as on a pad, so two
devices' windows for that frame can be read side by side without a merged
stream and without anything reconciling clocks afterwards.

That guarantee is the only reason this exists. It is not a router, a registry,
or a place to put device logic.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable

from blitspersecond.common import fps
from blitspersecond.input.kbm import KeyboardIngress, MouseIngress
from blitspersecond.input.pads import Pads

if TYPE_CHECKING:
    from blitspersecond.internal.events import EventBus
    from blitspersecond.internal.window import PresentationViewport


class InputBoundary:
    """Every input device the engine owns, cut on one frame boundary."""

    __slots__ = ("keyboard", "mouse", "pads")

    def __init__(
        self,
        events: EventBus,
        rate: fps,
        viewport: Callable[[], PresentationViewport],
    ) -> None:
        # Constructed with the engine and held by it, before the window
        # exists. The bus is what allows that ordering: an ingress subscribes
        # to an event type, not to a Surface, so nothing needs rewiring when
        # the window comes up and starts teeing into it. Holding matters --
        # the bus keeps handlers weakly, and an ingress nothing holds
        # unsubscribes itself when it is collected.
        self.keyboard = KeyboardIngress(events, rate)
        # The pointer needs to know where the picture is, which the window
        # alone can answer -- so it takes a callable resolved at the cut
        # rather than a Display it could not have been given yet. Passing a
        # device's dependency through is this object's job; deciding anything
        # with it is not.
        self.mouse = MouseIngress(events, rate, viewport)
        # Pads take neither the bus nor the window: their transport is the
        # kernel and they find themselves. What they share with the other two
        # is the only thing this object is for -- the cut below.
        self.pads = Pads(rate)

    def cut(self, frame: int) -> None:
        """Settle every device's interval. Called once per frame by `Loop`."""
        self.keyboard.cut(frame)
        self.mouse.cut(frame)
        self.pads.cut(frame)

    def flush(self) -> None:
        """Reconcile every device with reality after a gap in ticking.

        **The devices answer this differently, and the difference is the whole
        point of it being one call.** Wall time passed that no frame accounts
        for, so what accumulated meanwhile describes a game nobody was
        playing -- every device discards that. What they cannot agree on is
        what is true NOW:

        - the **keyboard assumes neutral**, because there is no way to ask it
          what is still down, and a key let go during the pause would stay
          held for good otherwise;
        - the **pointer keeps its position**, which is state rather than
          history and is wherever it is whether or not anybody was watching;
        - **pads ask**, because they can. A player holding a direction
          through a pause still holds it, and synthesising a release they did
          not perform would be inventing input.

        One call, three answers, each correct for its device. See
        `bps/reference/input/keyboard.md`, on reconciling against ground truth.
        """
        self.keyboard.flush()
        self.mouse.flush()
        self.pads.flush()

    def close(self) -> None:
        """Release what the devices hold. Safe to call twice.

        Only pads own anything: the keyboard and the pointer are subscriptions
        to a bus that is going away anyway, where a pad holds an open file
        descriptor per device.
        """
        self.pads.close()
