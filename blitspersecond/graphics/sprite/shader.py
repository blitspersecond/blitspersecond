"""The sprite pillar's shader program: many quads, many materials, one pass.

`quads.vert` + `indexed.frag`. The vertex stage is this pillar's OWN as of
2026-09-02: it reads no vertex attributes at all and rebuilds each quad from
a shader storage buffer indexed by `gl_VertexID / 4`. The tilemap and console
keep `default.vert`.
Why these live beside the code that issues them rather than in a keyed
`Shader.get(vert, frag)` cache is argued at length in
`graphics/console/shader.py`; the same reasoning applies and is not repeated.

## One program, not two

The tilemap carries an `indexed`/`direct` pair because a tile sheet may be
RGBA -- fog, cloud, high-colour backdrops, which nothing asks collision
questions about. **A sprite sheet is indexed by construction.** A package's
tilesets are validated indexed at load, and the grid road refuses a
non-indexed image outright and points it at the tile pillar. So there is no
branch here and nothing to choose: unit 0 carries palette indices, unit 1 the
tileset's whole bank as a 256xN table from
`graphics.internal.PaletteTexture`, and `palette_row` says which row of it to
read.

That is not a limitation being accepted quietly. Palette indices are what make
the pillar's colour model work at all -- per-presentation palette registers, a
recolourable character with pinned effects, a mirror match costing one sheet.
An RGBA sprite would have none of it.

## What is per draw here, and why there is more of it

`colors` is a generic vertex attribute (`glVertexAttrib4f`, no buffer), and
the tilemap sets it ONCE per layer. This pillar sets it **per run**, because
colour registers are per presentation -- a shadow trail's fade is four
presentations carrying four values, which is four runs sharing one texture
bind. Same mechanism, used at a finer grain.

`palette_row` is the same shape and arrived for the same reason. A costume
swap between two presentations used to be a second texture on unit 1; it is
now a number, because the bank they both belong to is one table.
"""

from typing import Optional

from pyglet.graphics.shader import Shader, ShaderProgram

from blitspersecond.common import PACKAGE_ROOT

_SHADERS = PACKAGE_ROOT / "assets" / "shaders"

_program: Optional[ShaderProgram] = None


def program() -> ShaderProgram:
    """The sprite pillar's compiled program, built on first use.

    Lazy because compiling needs a current GL context and importing must not:
    a sprite can be constructed before there is a window, and only the draw
    reaches this.
    """
    global _program
    if _program is None:
        vert = (_SHADERS / "quads.vert").read_text(encoding="utf-8")
        frag = (_SHADERS / "indexed.frag").read_text(encoding="utf-8")
        _program = ShaderProgram(
            Shader(vert, "vertex"),
            Shader(frag, "fragment"),
        )
    return _program
