# The clock

What a frame IS: the fixed-step simulation clock, and the unit every tuned constant in a game is really expressed in.

Not written. What it owes:

- the frame as the unit of time, and `time = frame * step` exactly
- why this module imports nothing from `time`
- who owns 60, and what a configurable rate would change
- determinism: what a replay guarantees and what it does not
- OWED TO pacing.md: the frame-as-unit argument, cited from its deadline section
