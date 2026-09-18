# Display

Where the picture lands, and where the events come from. Display is the
engine's entire boundary with the window system, and it is crossed in both
directions: a composed frame goes out to a panel, and the window system's
events come back in to whoever asked for them.

**It has no game-facing surface at all.** A game never names the display. It
composes through `bps.layers`, reads input off the bus, and the engine holds
the one window on its behalf — so what follows is how the machine works and
what it costs, not an API to call.

This runs outward first: the buffer, the space it declares, the enlargement
onto the window, the single flip that reconciles the engine's axis with GL's,
and the pass that lights the panel. Then inward, to the window as an event
source and the seam that keeps every other subsystem from knowing a window
exists.

## What this chapter owns

The published surface is three verbs and two properties, and each caller
reaches for exactly one part:

| caller | reaches for | and describes |
|---|---|---|
| the compositor | `display.pending`, `Framebuffer.as_target()` | why it composes into a handed-over target — [layers.md](../graphics/layers.md) |
| the loop | `display.frame_complete()` | why the flag falls where it does in the iteration — [loop.md](loop.md) |
| the pacer | `display.present()`, `display.swap_duration` | why a present happens when it does — [pacing.md](pacing.md) |

So the buffer, the handoff and the counters are described here; the callers
describe their use of them.

Inbound, **display owns transport and never interpretation**. This chapter
says the Surface is the sole producer of raw OS events, how they are teed onto
the bus, and why the bus republishes pyglet's whole window event set. What any
individual event *means* belongs to [lifecycle.md](lifecycle.md) or to
[input](../input/input.md).

## Limits and costs

### Geometry

| | |
|---|---|
| internal screen | 640x360, fixed |
| window | 2560x1440 — the internal screen at a scale fixed at 4 |
| enlargement | largest centred whole number that fits the window, recomputed every present |
| below 1:1 | refused, not scaled down |
| fullscreen | borderless at the desktop mode by design; not reachable today |

### Memory

| | |
|---|---|
| the compose target | 640x360 RGBA — **0.88 MiB, and the only buffer BPS allocates** |
| the window's own pair | 2560x1440 RGBA, 14.06 MiB each, 28.1 MiB together — the window system's, at any scale |

The engine's share of the picture is under a megabyte. Everything larger
belongs to the window and would exist whatever BPS did.

### Costs

Measured 2026-09-10 on a Mesa/radeonsi box under XWayland, on a synthetic
present loop rather than a game, so read them as the cost of the pass and not
of a frame. CPU submission time, not GPU completion.

| | |
|---|---|
| the present draw | 0.047ms p50 — viewport, clear, the quad |
| the swap | 0.059ms p50 here; 0.032ms on Win32/DWM |
| a bare clear into the compose target | 0.040ms p50 |
| a second compose buffer | **−0.010ms** — it costs slightly more than it saves |

The present pass carries no per-frame state of its own: the quad is built
once and the CRT uniforms are uploaded once, with the two that depend on
window size rewritten only when it changes. Rebuilding them every present
instead costs **0.071ms**, which is 59% of the draw.

[pacing.md](pacing.md) owns what the swap figure means for presentation
health; the number is here because it is the display's to produce.

### What refuses

| | |
|---|---|
| a window smaller than the internal screen | `ValueError: 320x200 surface is smaller than 640x360 internal resolution` |
| a zero or negative dimension | `ValueError: presentation dimensions must be positive` |
| composing after the display closes | the GL context is gone and the draw raises; the loop checks `closed` and returns instead |

Note what does **not** refuse: presenting when nothing new has been composed.
That is a normal present over an unchanged buffer, not an error.

---

## One buffer, and the pair behind it

The display owns one framebuffer at the internal resolution: 640x360 RGBA,
0.88 MiB, built once and alive for the process. The compositor is handed it,
writes it, and says when it is finished; the display draws it onto the window
and swaps. `display.pending` is the whole of what the engine sees of it.

It is cleared at construction, because presentation starts before the first
tick completes — the clock owes frame one a full step, so something is shown
before anything is composed, and black is a better something than whatever
the driver left in the texture.

Behind it the window carries its own front and back at window size, 14.06 MiB
each at the 4x scale, and `flip()` promotes that pair. **Nothing sits between
the two.** There is no staging buffer, no intermediate copy, and no
`glBlitFramebuffer` anywhere in the engine — the only pixel movement in a
present is one textured quad, which scales, flips and grades in the same
pass.

### `ready` is what a present asks about, not the buffer

`complete()` raises a flag; `promote()` lowers it and reports whether the
picture moved. Everything the pair of counters describes is a property of
that flag rather than of how many buffers exist: composing twice with no
present between overwrites a frame nobody saw, and presenting twice with no
compose between re-shows what is already up. Both are reachable with one
buffer. What they actually count today is a separate question from what they
mean, and the next section but two answers it.

### Why one is enough

Two things have to be true, and both are structural rather than lucky.

**The order.** Present strictly precedes compose within an iteration, on one
thread, in one GL context. The compose's write is therefore behind the
present's read in the command stream and cannot overtake it. This is
correctness, and it is the loop's to keep — see [loop.md](loop.md).

**The distance.** Ordering alone would still let the driver pay for the
dependency, by stalling the pipeline or ghosting the texture. It does not
have to, because by the time the CPU has finished a tick and issued the next
compose, the GPU has long finished a single quad. That is the CPU-bound
engine working in the drawing path's favour: the read is done not because
nothing is asynchronous, but because the GPU is far ahead of us.

Measured 2026-09-10 on Mesa/radeonsi under XWayland, both arms interleaved in
one session, 1500 iterations a population after 300 discarded. A second
buffer is worth **−0.010ms** in the adversarial case — no tick at all, 4270
iterations a second, the CPU issuing a full-surface clear the instant
`flip()` returns — and **nothing measurable** once 4ms of tick work separates
the read from the write. If the driver were ghosting a 0.88 MiB texture it
would show in the compose, and the compose is flat. Win32/DWM is a different
present path and is unmeasured.

### The edit that would need a second buffer

Compose overlapping present, and only that. **If compose moves off this
thread, or the loop ever composes before presenting within one iteration, the
write can reach the texture while the draw is still reading it and the buffer
has to double.** Nothing else about the handoff changes if it does — the
flag, the counters and the contract are all independent of the count.

## The buffer declares its own space

A caller draws into the handed-over buffer through one context manager:

    with fbo.as_target():
        ...

which binds it, establishes a 1:1 orthographic projection over the buffer's
own size with a viewport to match, and unbinds on the way out however the
block exits. The compositor is its only caller.

**The space is declared rather than inherited**, every time. The present pass
leaves framebuffer 0 bound and a window-sized viewport behind it, so whatever
GL state a compose begins in says nothing about where the internal screen is.
Establishing it from `self.width` and `self.height` rather than trusting what
is current is what makes a compose independent of when in the frame it
happens, and of whether anything was presented before it.

The projection it sets is the plain, uninverted one — `(0, width, 0, height,
-1, 1)`. That is what leaves GL-side content stored upside down, and it is
deliberate — see *One space, and therefore one flip*.

**One wrinkle is pyglet's and worth knowing before editing this.** The
projection is a *window*-level uniform — the `WindowBlock` block every vertex
shader in the engine declares — so establishing the buffer's own space is
done through `self._surface.projection` even though the space belongs to the
buffer. That is the only reason a `Framebuffer` holds a surface reference at
all.

### What a caller does not learn

The window's size, the scale it will be enlarged by, whether this frame will
be shown, and when. A caller says "draw into this buffer" and acquires no
part in presenting it — which is what lets the compositor be written without
a presentation model in it, and lets the present pass change without a
compositor edit.

The clear sits outside the block, on its own bind, which is why **an empty
draw order costs exactly one clear**: a game that has added no layers pays a
clear and a swap and nothing else.

## What the handoff counts

`FrameHandoff` keeps two tallies, and **neither is published**. They are
diagnostics on the mechanism, readable by a probe reaching through
`bps._display`, and they are zero by construction in ordinary running.

| | |
|---|---|
| `dropped` | a finished frame composed over unshown. Rises only while backgrounded, where the loop keeps composing and stops presenting |
| `repeated` | a present with no new frame ready. Rises only after a tick that returned early on a closed display |

Both are zero because the loop presents exactly once per tick, so
`complete()` and `promote()` alternate strictly — `complete()` always raises
`ready`, and the next `promote()` always lowers it. There is no third
outcome.

**They are not the rate crossover, in either direction.** Above it the engine
never presents into the extra slots at all: the panel re-scans what it holds
and BPS never learns of it. Below it, presents arrive faster than the panel
can show them and the *compositor* discards the surplus, which is invisible
from here. Neither side is this handoff holding or dropping anything, and a
description of these counters that promises otherwise is describing a
crossover the loop does not have.

**The faults that do rise are the pacer's**, published as
`display.frames.overran`, `display.starts.late` and `display.phase.shifts`.
[pacing.md](pacing.md) owns what each means and how to read them together.

### There is no re-present path

`promote()` either lowers the flag or finds it already down, and the draw
runs identically either way. One code path per present, and the only variable
is whether a new frame preceded it. **A re-present is not a special case in
the code and should not be read as one** — it is the same present, over a
buffer nothing has written since.

### Promotion is late

Promotion happens immediately before the draw, never at the moment a frame is
flagged complete. The reason on record is a full engine frame of latency, and
it does not survive tracing this loop: present precedes compose within an
iteration, so a frame flagged at the end of one iteration is shown on the next
under either arrangement. With one buffer `promote()` exchanges nothing at
all, so its timing reaches `promoted` and nothing else.

**The rule stands anyway**, deliberately: it is free, it costs nothing to
keep, and a loop shaped differently would need it. What it does not have is a
measurable cost today, so do not restate one.

## The integer viewport

The internal screen lands on the window at the **largest whole-number
enlargement that fits, centred**. At a 4x scale and a 2560x1440 window that is
exactly 4 and fills it; at any other window size the picture is centred and
the remainder is left black.

It is computed from `surface.get_size()` on every present, so a window that
has been resized needs no notification and there is no cached geometry to go
stale. The present pass clears the whole window black, then sets the viewport
to the centred rect — which is where the letterboxing comes from, rather than
from anything drawing bars.

**Presentation never shrinks the internal screen.** A surface smaller than the
logical target cannot give an integer mapping, so rather than pick a
fractional one it refuses:

| | |
|---|---|
| any dimension `<= 0` | `ValueError: presentation dimensions must be positive` |
| window smaller than internal | `ValueError: 320x200 surface is smaller than 640x360 internal resolution` |

**One function owns the mapping, and both directions now go through it.**
The present pass calls `integer_viewport` to map the internal screen out to
window pixels. The pointer comes back the other way from the same answer:
`Display.viewport` recomputes the viewport from the surface's current size,
the engine hands that to the input boundary as a callable rather than as a
value, and the pointer resolves it once per cut. Two functions would be two
chances to disagree about where the picture is, and a disagreement there is a
cursor that does not land where it points — visible only as a slight,
unreproducible offset.

**The inbound direction inverts the viewport rather than calling the same
function**, and it does two things the outbound one has no reason to: it
flips y, pyglet's origin being bottom-left, and it clamps per axis so a
pointer out in the letterbox margin slides along the edge it left through
instead of stopping dead. Both belong to the chapter that owns the pointer:
`bps/reference/input/pointer.md`.

`scale` is also what the CRT pass keys its scanline period to, so the look
follows the enlargement rather than being fixed in device pixels.

## One space, and therefore one flip

*(§5 — drafted. The sections around it are still brief.)*

BPS places everything in screen pixels: 640x360, origin top-left, y increasing
downward. OpenGL's clip space puts the origin at the bottom left with y
increasing upward. The two conventions meet in one line of one shader, at the
last boundary before the panel:

    TexCoord = vec2(in_tex_coords.x, 1.0 - in_tex_coords.y);

That is the whole of it — `blitspersecond/assets/shaders/framebuffer.vert`,
on the four
corners of the quad that presents the composed buffer. No image is stored,
loaded or stamped inverted to compensate, and no other stage flips anything.

**One flip is enough because there is only one space to flip.**

### Nothing installs a transform

`window.view` is never assigned anywhere in the engine; it is identity in
every shader that declares it. `window.projection` is written in exactly one
place, `Framebuffer.as_target()`, and it is a plain orthographic projection
over the buffer's own size — `(0, width, 0, height, -1, 1)`, with no inversion
in it.

So a quad reaches the vertex stage already carrying its final screen
coordinates. `quads.vert` fetches a rect from a buffer texture and emits it;
`picture.vert` adds a translate; `default.vert` composes a scale and a
rotation about the quad's own origin. None of them converts between spaces,
because there is no second space to convert from.

### A camera is not a space

The tilemap has a camera, and `graphics/tilemap/collision.py` is written in
its vocabulary throughout — drift, apron, a plane walking under a moving view.
None of it reaches the GPU as a transform. A scroll resolves on the CPU into a
destructive move of the stored plane plus one vertex attribute, both in screen
pixels, so what a vertex stage receives is a rect at its final position like
every other rect.

The game-facing vocabulary states the same commitment out loud: a pan is not
moving a camera right, it is moving stuff off the screen left. A camera is an
internal convenience, never a coordinate system the rest of the engine has to
know about.

### GL-side buffers are uniformly upside down

Content authored top-left and drawn through an uninverted ortho lands in the
colour texture inverted: the engine's top row is stored in the texture's row
0, which GL considers the bottom. The present pass is what puts it right way
up.

Kris, 2026-08-28: *"the shader flips it when drawing to screen; so if you
sample any internal buffer they will be upside down — it's expected, it
doesn't matter so long as everything gl side is consistently upside down
before present."*

So **sampling any internal buffer returns an inverted image, and that is
correct.** A readback taken before the present pass needs exactly one flip,
known in advance — and misses everything else the pass applies besides.

The edit to refuse is a local correction. Flipping one texture, one loader or
one stamp so that buffer reads right way up on its own puts it out of step
with every other GL-side buffer, and the boundary flip then inverts it into
being wrong. Consistency before present is the invariant; individual
correctness is not.

### The flip is exact

Texel centres in a buffer of height `H` sit at `(i + 0.5) / H`. Subtracting
from one gives `(H - 1 - i + 0.5) / H`, which is the centre of row
`H - 1 - i`: centre to centre, every row, at any height. No sample lands
between texels, so the flip resamples nothing and introduces no half-pixel
drift, and the nearest filtering and integer viewport that carry the image
onto the panel keep it that way through the enlargement.

### Inverting the projection instead

The alternative a reader arriving from ordinary GL will assume is to build
`as_target()`'s ortho as `(0, width, height, 0, -1, 1)`. The picture comes out
the right way up with no shader involved, and no inverted buffers to reason
about.

The usual objection to a mirror does not arise here. Everything BPS draws is a
flat plane, and a plane has no front or back: `GL_CULL_FACE` is never touched,
no stage asks which way a triangle faces, and there is no winding contract for
a mirror to break. Each pillar mirrors however suits it — sprites and tiles
permute which corner reads which `(u, v)`, the console materialises turned
variants and gathers them in numpy — and nothing downstream can tell the
difference.

**The real cost is that an inverted projection only fixes the GPU half.**
The collision plane is a CPU-side bitmap in the same screen space — `row 0 =
top`, packed by numpy, never through GL at all — and a projection matrix
cannot reach it. An inverted ortho puts the picture the right way up while
the plane's row order stays defined against the buffer as drawn, so picture
and mask disagree by a vertical mirror. The fix is a second flip somewhere a
matrix cannot express. Two flips in two subsystems, each defensible alone,
is the many-places arithmetic that one flip at the boundary exists to end.

### What it buys

Collision coordinates and screen coordinates are the same coordinates. A query
is "screen pixels, `(0, 0)` top-left: collides means visually" and an atlas
rect is "origin top-left", and neither needs qualifying against the other,
because nothing between them flips. That is what lets a game ask whether a
sprite touches a painted trigger at the position it drew it, and what lets
`collides` mean visual overlap rather than overlap in a mask space that
happens to correspond.

## The rest of the present pass

What happens between `present()` and a lit pixel, after the flip described
above: the window is cleared black at its full size, the viewport is set to
the centred rect, and one full-screen quad is drawn, textured from the
composed buffer. Nearest filtering at both minification and magnification, so
an enlarged pixel stays a hard square. Depth test and blend are both disabled
— there is one quad and nothing to blend it with.

Then the CRT look, which is a **fixed house aesthetic rather than per-game
configuration**: a scanline darkening every `scale` rows to 80% over the
thinnest fifth of the period, a colour multiplier of `(1.1, 1.05, 0.9)`,
brightness 1.1, contrast 1.2, gamma 0.9, and a noise dither at 2%.

Two things about it are worth knowing before changing it. **The scanline
period is `viewport.scale`**, so the effect scales with the window instead of
being fixed in device pixels — at 1x there is no room for it and at 6x it is
six rows apart. And **the noise is static**: it is a hash of `gl_FragCoord`
alone with no time input, so it is a fixed dither pattern rather than moving
grain, and it is identical in every frame.

Anything sampling a buffer before this pass gets none of it — see the note on
readbacks under *One space, and therefore one flip*.

---

## One window, and its lifetime is the engine's

The engine creates exactly one window — a `Surface`, in BPS vocabulary — and
its lifetime is the engine's. The window being up is part of the engine being
ready; the window closing finalises the engine. `display.closed` breaks the
loop, which stops the lifecycle with *"display closed during event dispatch"*.

That window is the front of the chain from the first section. It carries its
own double buffer, and `flip()` is what promotes it.

**This is pyglet's own application model, and BPS agrees with it.** The
`pyglet.app` documentation opens by saying that most applications need only
call `run()` after creating one or more windows, and gives a one-window
program as the example. One window is the application. So `Surface` is a
plain `pyglet.window.Window` subclass and nothing more elaborate — the
ordinary way to use the library.

**The one seam is narrow and worth stating exactly: BPS takes pyglet's
`Window` and declines pyglet's `EventLoop`.** `pyglet.app.run()` is never
called; [loop.md](loop.md) owns the loop that stands in its place.

That single fact explains something that otherwise looks like defensive
plumbing. Pyglet's default `on_close` is wired to the global `app.event_loop`
— the one BPS never runs — so it reaches nothing, and the lifecycle observes
`on_close` on the bus instead.

## The tee, and the pump

The one thing that does not sit where pyglet would put it is the handlers.
Several subsystems want the same window events and none of them should name
the concrete `Surface`, so `Surface.dispatch_event` forwards every event it
dispatches onto an `EventBus`, and observers subscribe there.

**An addition to the window model rather than a replacement for it.** The
window is still the sole producer of raw OS events; what moves is only where
a handler attaches.

    Surface  ──tee──►  EventBus  ──►  Lifecycle  (on_close, on_hide, on_show,
                          │                       on_expose, on_activate)
                          ├──────►  KeyboardIngress  (gameplay keys)
                          ├──────►  MouseIngress     (pointer, buttons, scroll)
                          └──────►  BlitsPerSecond   (on_engine_state)

`Pads` is absent by nature rather than by omission: its transport is the
kernel, not the window, so it has nothing here to subscribe to. The two that
do subscribe are held by `InputBoundary`, which settles all three devices on
one cut — `bps/reference/input/input.md`.

**The tee fires exactly once per event, and the reason is not obvious.**
`dispatch_event` is overridden, but pyglet's queue-drain path
(`dispatch_pending_events`) calls `EventDispatcher.dispatch_event` directly
and bypasses the override. So a queued event reaches the bus through the
override once, rather than once when queued and again when drained. An edit
that "fixes" the override by also teeing from the drain path doubles every
queued event.

The bus registers pyglet's entire `Window.event_types` set, so republication
is faithful rather than curated — nothing decides here which events are worth
forwarding. There is effectively no cost while nothing is subscribed, because
pyglet builds a handler stack only on demand.

**`Display.dispatch_events()` pumps both queues in one call**: the surface's
own events, and pyglet's scheduled callbacks. The second is window-system
housekeeping and belongs at iteration rate — it is not a second engine clock,
and reading it as one is the mistake the single call exists to prevent. Step
2 of the iteration in [loop.md](loop.md).

## Three overrides, and three reasons

Where `Surface` departs from a stock `Window`, it does so in three places for
three unrelated reasons. They are not one policy, and reading them as one
invites a fourth that does not belong.

**`on_key_press` does nothing, deliberately.** Pyglet's `BaseWindow` default
translates an unmodified Escape into `on_close`. BPS reserves `on_close` for
genuine OS and window-manager close requests — the close button, Alt+F4, and
platform equivalents — so an empty override takes the default out of the way.
Escape reaches the game like any other key, over the bus. Deleting this
override gives a game whose Escape key quits it without being asked to.

**`close()` closes the surface and nothing else.** It marks itself closed and
releases the GL surface; it does not touch the loop or the frame schedule,
because it does not own them. `BlitsPerSecond` observes `closed` and performs
teardown. What that separation buys is one place where shutdown is ordered,
rather than a window that can pull the loop out from under whatever is
running.

**`dispatch_events()` is guarded for shutdown.** It does nothing once closed,
and an exception from pyglet's dispatch is logged and turns into `closed`
rather than propagating. Pumping a dead window is a normal thing to happen on
the way out — the loop checks `closed` after dispatch, not before — and a
raise there would turn an ordinary teardown into a traceback.

## What the Surface decides on its own

Some display policy cannot be teed to anyone, and the Surface owns it.

**Client vsync is off**, on every path, and `BPS_VSYNC=1` turns it back on.
Off is right because the simulation is not paced by the swap, so a blocking
flip buys the engine nothing and costs a compositor that charges a full extra
refresh for a narrowly missed vblank. The switch exists because a blocking
flip returns *at* a vblank and is therefore a free phase reference on
platforms that genuinely block — which native X11 does, and which is still
unvalidated.

Measured 2026-08-27 on Win32/WGL under DWM, it does nothing there: pyglet
applies it and `window.vsync` reads True, but the swap is unchanged at
0.032ms, because the present mode is *Composed: Copy with GPU GDI* — DWM
composites on its own vblank and the client `SwapBuffers` returns
immediately. There is no blocking swap to be had on that path, and the phase
reference comes from `DwmGetCompositionTimingInfo` instead.

**Fullscreen is borderless at the active desktop mode, never exclusive.**
BPS never owns display modes: passing `fullscreen=True` together with a
logical size makes pyglet call `ChangeDisplaySettingsEx` on Windows and
select an exclusive low-resolution mode, which is a mode change the engine
has no business making. The borderless path sets the window to the screen's
own size and position instead.

**Today the window is one size and windowed.** The scale is fixed at 4, so
640x360 becomes 2560x1440, and the fullscreen branch is not reachable. Both
wait on file-backed configuration; this section is the one that changes when
it lands.

**The window is named `BlitsPerSecond` until a game names it.** Pyglet's
default caption is `sys.argv[0]`, which puts `main.py` in the taskbar and says
nothing about what is running; the engine's own name is the only honest answer
available before a game has given one. A game replaces it through
`bps.title`, which forwards to `set_caption` and reads back off
`Surface.caption` — the window already holds the string and the window manager
already holds a copy, so a third on `Display` could only be the one that goes
stale, and it would go stale invisibly until somebody read the title back and
disagreed with the title bar. Nothing is compared against the previous value
either: each assignment is a call into the window system, so a title updated
every frame is a window-system call every frame.

**The refresh rate is a property of the window, not of the screen.**
`Window.screen` is fixed at construction, so it answers where the window
started rather than where it is. Win32's `get_window_screen()` uses
`MonitorFromWindow`, which is the operating system's current monitor
assignment for that HWND, so that is what gets asked. Ordinary moves and
`WM_DISPLAYCHANGE` mark the cached rate dirty rather than acting; a
foreground restoration forces a check outright, because a change may have
happened while event delivery was unavailable.

A failed topology query clears DWM eligibility rather than keeping the last
answer. An accepted absolute clock left attached to a window whose output is
now unknown is worse than no clock.

**The DWM clock needs the primary output.**
`DwmGetCompositionTimingInfo` has exposed one NULL-HWND desktop clock since
Windows 8.1, and the reference capture establishes it for the primary output.
A secondary at the same rate is not evidence of the same phase, particularly
across adapters, so eligibility is Win32 plus primary and nothing less.

## Still open

- **The window is one size and windowed**, pending file-backed configuration.
  *What the Surface decides on its own* is the section that changes.
- **One buffer is proven here and open on Windows.** Win32/DWM is a
  materially different present path and the measurement has not been repeated
  there.
- **`FrameHandoff` has no test coverage at all**, live or otherwise. It is
  the one piece of the handoff nothing asserts.
