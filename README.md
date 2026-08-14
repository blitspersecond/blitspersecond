# Blits Per Second

Blits Per Second is a work-in-progress 2D pixel-art engine for modern Python.
It is deliberately narrow: games render to a fixed 640x360 internal screen,
using explicit, hardware-flavoured systems rather than a general-purpose scene
graph.

The engine is being built around:

- indexed, palette-oriented graphics;
- flat, ordered layers;
- tile, sprite and glyph rendering;
- predictable fixed-tick simulation and presentation;
- direct keyboard, mouse and gamepad input; and
- a programmable real-time audio engine.

BPS is intended to make small console- and arcade-style games pleasant to
tinker with while keeping the engine's timing and resource costs visible.

## Status

BPS 3 is under active development and is not ready for a general release.
Public APIs, packaging and platform support may still change. The maintained
examples in `examples/` show the small public surface that currently exists;
larger exploratory programs live in the separate experiments repository.

The current development baseline is Python 3.14. Runtime dependencies are
listed in `pyproject.toml`.

## Licence

Blits Per Second is copyright 2026 Kris Holdich and released under the
[MIT Licence](LICENSE).
