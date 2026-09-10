# Examples

These are polished, maintained demonstrations of the current public API.

**An example earns its place by being driven by a page in `bps/guide/`.** Not
by covering a pillar, and not by being a large program that happens to
exercise the engine — a demo with no page teaching from it is one nobody is
reading and nobody notices has broken. Exercising a subsystem is what the
`testsuite` fixtures are for, and they are measured against the screen rather
than read.

So a pillar with no guide page has no example here yet, and that is the
correct state rather than a gap.

- `console.py` — the smallest complete console-input program
- `pen.py` — flat pens, facing, and where a gradient comes from
  (the engine deliberately does not build ramps for you)
- `glyphs.py` — the advanced road: a blank layer with glyphs you invent,
  direct cell writes and runs, and a console layered over the top
- `gamepad.py` — move a sprite through the direct gamepad surface, off a
  bare image read as a sprite grid
- `tmx.py` — load a compatible TMX map and stamp its island onto a
  tile layer

On Linux, run an example through the normal launcher:

    util/linux.sh examples/console.py

It selects the repository virtualenv and uses Gamescope when available.
`--direct` bypasses Gamescope; `--mangohud` adds the performance overlay;
`--fullscreen` selects fullscreen explicitly.

Exploratory programs without this compatibility promise live in a separate,
public but unlicensed experiments repository. Larger programs imported as
behavioural fixtures live in `tests/examples/` until they receive a deliberate
teaching-surface tune-up.
