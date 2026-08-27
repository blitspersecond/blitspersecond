"""WHEN to swap.

This package used to answer "when to tick" as well, and that was the larger
half of it: a fixed-tick simulation had to be distributed over presentations
arriving at the display's rate rather than the game's, and getting it wrong
meant the game ran at the wrong SPEED. The engine now holds the authored rate
on its own clock (`blitspersecond.clock`), so presentation timing and
simulation timing are independent and neither divides the other. What is left
here is display's own question.

The chain that remains:

  path.py          WHAT KIND OF PATH IS THIS?
                   Classifies the environment from launch facts alone --
                   Gamescope, direct XWayland, X11, Win32. It no longer
                   STEERS anything: every path presents on a deadline grid
                   with client vsync off. It describes, for the log and for
                   frame-pacing captures, which are only comparable against
                   one another if they say what produced them.

  refresh_rate/    WHAT RATE DOES THIS OUTPUT CLAIM?
                   Straight from the platform. Win32's pyglet mode rate is
                   integer-only and truncates 120000/1001 to 119, so this
                   prefers the rational rate from the active display path
                   there and falls back to pyglet elsewhere. Used ONLY to
                   pace presentation now, so being slightly wrong costs an
                   uneven present pattern rather than a game at the wrong
                   speed.

  health.py        IS THE COMPOSITOR STILL TAKING FRAMES?
                   One direction only: presentations withheld from an
                   occluded or minimised window (~2s grants observed under
                   Mutter). The other direction it used to watch for -- the
                   swap not blocking -- is now the normal case everywhere and
                   evidence of nothing.

  session.py       THE RUN.
                   Display's presentation deadline for one invocation of the
                   machine loop: sleeps and spins to it, publishes it so the
                   loop can wait on whichever of its deadlines falls first,
                   re-paces when a window moves to another output, and feeds
                   the optional frame-pacing sink.

What went, and why it is not coming back:

  cadence.py       Divided presentation slots into game ticks as a small
                   rational. Nothing divides them now.
  rate.py          Measured the delivered rate so that division stayed
                   honest across output changes. A present interval on a
                   non-blocking path measures our own timer, not the panel,
                   so it could not run here even if the answer were wanted.

Both are recorded in full, with the research behind them, in
`docs/bps/DECOUPLED_PRESENTATION.md`. The numbers quoted above are observed
rather than assumed; `docs/multi-display-findings.md` is the authority.

The spatial counterpart -- WHERE the internal screen lands on the window --
is `display.internal.viewport`, and is a much simpler problem.
"""
