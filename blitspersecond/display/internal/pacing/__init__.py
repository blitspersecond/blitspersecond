"""WHEN to tick, and WHEN to swap.

A fixed-tick simulation has to be distributed over presentations that arrive
at the display's rate, not the game's. Getting it wrong is not subtle -- the
game runs at the wrong SPEED. And almost nothing here can simply be asked:
the platform either does not report it, reports it stale, or reports a
plausible wrong number. So each fact has to be established, and each one gets
its own module, its own evidence, and its own guard.

The chain, each link answering what the next one needs:

  path.py          WHAT KIND OF PATH IS THIS?
                   Classifies the environment from launch facts alone --
                   Gamescope, direct XWayland, X11, Win32. Settles two things
                   nothing downstream can: whether to ask the window system
                   to pace swaps at all, and whether to start on a deadline
                   grid or trust a blocking swap. A pure function of the
                   environment.

  refresh_rate/    WHAT RATE DOES THIS OUTPUT CLAIM?
                   The nominal answer, straight from the platform. Win32's
                   pyglet mode rate is integer-only and truncates 120000/1001
                   to 119, so this prefers the rational rate from the active
                   display path there and falls back to pyglet elsewhere.
                   The claimed rate is what a measured one gets checked
                   against -- so it lives beside `rate.py`, not apart from it.

  rate.py          WHAT RATE IS ACTUALLY PACING US?
                   The rate is a property of the output the window happens to
                   be on, and moving the window does not announce itself. So
                   it is MEASURED from delivered present intervals, behind
                   two guards that stop the measurement lying: a steadiness
                   test (a window straddling a 144Hz and a 60Hz output yields
                   a 69Hz median -- a rate no display runs at) and a
                   candidacy test against the claimed rates above (which
                   separates "moved to a 60Hz display" from "dropping every
                   other frame on a 144Hz one" -- both deliver 60/s, only one
                   should re-cadence).

  health.py        IS THE STREAM BEHAVING?
                   Classifies swap durations into a verdict. The
                   one-iteration-per-refresh assumption breaks in two
                   directions: the swap stops blocking (loop spins at kHz and
                   fast-forwards the game) or presentations get withheld
                   (occluded window; ~2s grants observed under Mutter). Both
                   require a STREAK, never a single sample -- one fast flip is
                   a cheap frame, one slow flip is a hiccup.

  cadence.py       SO WHEN DO WE TICK?
                   Turns target-rate-over-display-rate into a small rational
                   and distributes ticks by phase accumulator -- 60-on-120 is
                   one tick every two presentations. Deliberately NOT
                   wall-time accumulation, so a slightly slow presentation
                   stream makes the game slightly slow rather than scattering
                   ticks onto adjacent frames.

  session.py       THE RUN.
                   Owns one invocation of the machine loop: holds the four
                   above, sleeps/spins to the deadline when the swap is not
                   pacing us, re-cadences when the measured rate changes, and
                   handles the lifecycle edges where accumulated evidence has
                   to be thrown away (backgrounded, foregrounded, resumed --
                   an interval spanning a suspension describes the pause, not
                   the display).

Everything except `session.py` is pure: no window, no GL, no clock. That is
deliberate, and it is what makes the guards testable at all. The numbers
quoted above are observed rather than assumed;
`docs/multi-display-findings.md` is the authority.

The spatial counterpart -- WHERE the internal screen lands on the window --
is `display.internal.viewport`, and is a much simpler problem.
"""
