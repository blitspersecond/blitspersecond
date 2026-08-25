"""Observable engine lifecycle state machine."""

from __future__ import annotations

from enum import Enum, auto

from blitspersecond.system.events import EventBus


class EngineState(Enum):
    """Lifecycle of the engine's main loop.

    Transitional states (STARTING, SUSPENDING, RESUMING, STOPPING) are
    momentary: they exist so observers of ``on_engine_state`` get a hook
    *during* the transition and always settle into the adjacent settled state
    within the same loop iteration. Game-level pause is NOT an engine state --
    it's a mode the game runs while the engine is RUNNING. Audio is its own
    clock domain and deliberately continues across suspension.
    """

    STOPPED = auto()
    STARTING = auto()
    RUNNING = auto()
    SUSPENDING = auto()
    SUSPENDED = auto()
    RESUMING = auto()
    STOPPING = auto()


# Engine-domain events. Lifecycle state is explicit engine control; background
# and foreground report whether the compositor is currently presenting the
# application, without imposing a game-level pause policy.
EventBus.register_event_type("on_engine_state")
EventBus.register_event_type("on_background")
EventBus.register_event_type("on_foreground")
