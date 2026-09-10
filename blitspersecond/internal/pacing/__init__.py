"""WHEN to swap, and how long a second lasts.

Display's own question. The engine holds the authored rate on its own clock,
so presentation timing and simulation timing are independent and neither
divides the other -- what this package decides is which slot a finished frame
belongs on, and when the engine has to start building the next one.

  path.py           WHAT KIND OF PATH IS THIS?
                    Gamescope, direct XWayland, X11, Win32, classified from
                    launch facts alone. It STEERS nothing: every path presents
                    on a deadline grid with client vsync off. It describes,
                    for the log and for captures, which are comparable only if
                    they say what produced them.

  refresh_rate/     WHAT RATE DOES THIS PANEL RUN AT?
                    The most exact answer the platform will give. Pyglet
                    reports an integer on both platforms that matter, so
                    Win32 reads a rational rate from the active display path
                    and X11 divides the CRTC's own mode line. Both are fixed
                    upstream and neither is in a release yet; this package
                    goes when one is.

  accepted_rate.py  WHAT RATE WILL THIS PANEL CARRY?
                    A rate that does not divide the panel leaves a frame with
                    nowhere to go, periodically, forever. This picks the
                    nearest rate whose grid IS commensurate. The simulation is
                    untouched; what moves is how long the pacer makes one
                    second last.

  dwm.py            WHAT CLOCK IS DWM ACTUALLY RUNNING?
                    Win32 only. A configured mode saying 144/1 against a live
                    QPC period of 143.998848Hz is enough for a private grid to
                    drift across a vblank boundary. Supplies period and
                    absolute phase; promises nothing about DWM's downstream
                    queue. Other paths never import it.

  health.py         IS THE COMPOSITOR STILL TAKING FRAMES?
                    One direction only: presentations withheld from an
                    occluded or minimised window. The other direction -- a
                    swap that does not block -- is the normal case everywhere
                    and evidence of nothing.

  sink.py           The optional capture's shape, stated as a Protocol so
                    nothing here imports the recorder.

  session.py        THE RUN. One invocation of the machine loop: sleeps to
                    each frame's slot, sleeps again before the engine has to
                    start, re-paces when a window moves output, and feeds the
                    sink.

Two things this package must never become, both of which look like
simplifications from inside it: something that grants ticks, and something
that polls every slot. `bps/reference/engine/pacing.md` is the authority --
the design, the measurements, the alternatives it refuses, and what is still
open. `docs/multi-display-findings.md` owns the multi-output observations.

The spatial counterpart -- WHERE the internal screen lands on the window --
is `internal/window/viewport.py`, and is a much simpler problem.
"""
