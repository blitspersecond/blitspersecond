"""The console's shader program: one whole-buffer quad, palette-indexed.

`picture.vert` + `indexed.frag`. The console composes its whole screen into a
single `PixelBuffer` of palette indices and draws it as one quad wherever it
sits -- no scaling, no rotation, no per-tile anything -- which is what the
picture vertex stage is for. `indexed.frag` does the palette lookup on the
GPU: unit 0 carries the indices, unit 1 the palette table from
`graphics.internal.PaletteTexture`. A console has one palette, so that table
is a single 256x1 row and `palette_row` is 0.

A program has ONE uniform state, so anything per-instance -- the palette row,
any tint -- is set per draw and never once at build.

Compiled here rather than fetched from a shared cache. Why the engine has no
such cache: `bps/reference/console.md`, "One quad to the screen".
"""

from typing import Optional

from pyglet.graphics.shader import Shader, ShaderProgram

from blitspersecond.common import PACKAGE_ROOT

#: Resolved against the PACKAGE, not the working directory: these are engine
#: assets shipped inside it, so a game that `pip install -e`'d the engine and
#: runs from its own directory still finds them.
_SHADERS = PACKAGE_ROOT / "assets" / "shaders"

_program: Optional[ShaderProgram] = None


def program() -> ShaderProgram:
    """The console's compiled program, built on first use.

    Lazy because compiling needs a current GL context and importing must not.
    A console can be constructed before there is a window; it cannot be drawn
    before there is one, and this is only reached from the draw.
    """
    global _program
    if _program is None:
        vert = (_SHADERS / "picture.vert").read_text(encoding="utf-8")
        frag = (_SHADERS / "indexed.frag").read_text(encoding="utf-8")
        _program = ShaderProgram(
            Shader(vert, "vertex"),
            Shader(frag, "fragment"),
        )
    return _program
