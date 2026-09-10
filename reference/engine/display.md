# Display

WHERE the picture lands: an engine's logical screen realised on a physical one.

Not written. What it owes:

- the framebuffer pair, promotion, and what dropped and repeated count
- the post-process pass and the house CRT look
- the integer viewport, and whole pixels at the last boundary
- the surface: what pyglet owns and what the engine decides
- OWED TO pacing.md: the buffer pair, cited from its swap section

## Read this before starting: it is two subjects, not one

The drawing half is settled and is a leaf — the framebuffer pair, the
viewport, the post-process quad, the present. That is what the list above is
mostly about, and it can be written straight.

**The window half is an integrator, and it is the origin of the event
system.** `Surface.dispatch_event` tees every pyglet event onto the
`EventBus`, so display is where input and lifecycle both get their raw
material:

    Surface  ──tee──►  EventBus  ──►  Lifecycle  (on_close, on_hide, on_show,
                          │                       on_expose, on_activate)
                          └──────►  KeyboardIngress  (gameplay keys)
                          └──────►  BlitsPerSecond   (on_engine_state)

`Display.dispatch_events()` pumps both the surface's queue and pyglet's
scheduled callbacks in one call, at iteration rate, from
[loop.md](loop.md)'s step 2.

Kris, 2026-09-10: *"display also leads into input because it's the event bus
pump and a large part of lifecycle."*

**So decide the boundary before writing, and say where it fell.** The
candidate split: this chapter owns the surface as an event *source* and the
tee as a mechanism; [lifecycle.md](lifecycle.md) already owns which window
events mean what to the engine, and an input chapter owns what happens to a
keystroke after the bus. What must not happen is the window events being
described twice, in two chapters that later disagree.

That also means the leaf/integrator split in
[the index](../index.md) is per-*subject*, not per-module — one module can be
both, and this is the module where that is most obvious.
