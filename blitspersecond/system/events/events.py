from pyglet.event import EventDispatcher
from pyglet.window import Window as _PygletWindow


class EventBus(EventDispatcher):
    """Central, surface-agnostic event hub.

    The ``Surface`` tees every raw pyglet event it dispatches into this bus
    (see ``Surface.dispatch_event``). Subsystems -- input, audio, etc. --
    subscribe here as observers via ``push_handlers()`` / ``@bus.event``, so
    they never take a direct dependency on the concrete ``Surface``.

    This is the observer seam: the pump (``BlitsPerSecond._run``) drains the
    surface's event queue, the surface republishes onto the bus, and observers react.
    There is effectively zero overhead while nothing is subscribed -- pyglet
    only builds the handler stack on demand.
    """

    pass


# Republish the full set of pyglet window event types so any of them can be
# observed on the bus without the observer knowing about the window at all.
for _event_type in _PygletWindow.event_types:
    EventBus.register_event_type(_event_type)
