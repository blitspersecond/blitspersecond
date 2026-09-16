from pyglet.event import EventDispatcher
from pyglet.window import Window as _PygletWindow


class EventBus(EventDispatcher):
    """The engine's central, surface-agnostic event hub.

    `Surface` tees every raw pyglet event onto this bus; subsystems subscribe
    here rather than to the concrete Surface. Effectively free while nothing
    is subscribed -- pyglet builds a handler stack only on demand.

    See `bps/reference/engine/display.md`, *The tee, and the pump*.
    """

    pass


# The FULL set, so republication is faithful rather than curated: nothing
# here decides which window events are worth forwarding.
for _event_type in _PygletWindow.event_types:
    EventBus.register_event_type(_event_type)

#: Where the audio actually is: the device name, or None while there is no
#: device at all and the engine is running silently. Published when it
#: CHANGES, which for most runs means never.
#:
#: Dispatched by the loop rather than by the audio worker that discovered it.
#: A handler runs on whichever thread dispatches, and the audio thread is the
#: wrong one twice over -- a slow handler there costs xruns, and game code
#: reached from it would be touching the game off the main thread.
EventBus.register_event_type("on_audio_device")
