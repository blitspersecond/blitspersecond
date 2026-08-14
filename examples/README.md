# Examples

These are polished, maintained demonstrations of the current public API. Each
one should be suitable for support by the eventual manual, rather than merely
being a large program that happens to exercise the engine.

- `hello_console.py` — the smallest complete console-input program
- `hello_gamepad.py` — move a cursor through the direct gamepad surface

On Linux, run an example through the normal launcher:

    util/linux.sh examples/hello_console.py

It selects the repository virtualenv and uses Gamescope when available.
`--direct` bypasses Gamescope; `--mangohud` adds the performance overlay;
`--fullscreen` selects fullscreen explicitly.

Exploratory programs without this compatibility promise live in a separate,
public but unlicensed experiments repository. Larger programs imported as
behavioural fixtures live in `tests/examples/` until they receive a deliberate
teaching-surface tune-up.
