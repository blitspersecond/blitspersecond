"""The sprite pillar's shader program: many quads, many materials, one pass.

`quads.vert` + `indexed.frag`. The vertex stage is this pillar's OWN: it reads
no vertex attributes at all and rebuilds each quad from a buffer texture
indexed by `gl_VertexID / 4`. The tilemap and console keep `default.vert`.

Why these live beside the code that issues them rather than in a keyed
`Shader.get(vert, frag)` cache is argued at length in
`graphics/console/shader.py`; the same reasoning applies and is not repeated.

## One program, not two

The tilemap carries an `indexed`/`direct` pair because a tile sheet may be
RGBA. **A sprite sheet is indexed by construction** -- a package's tilesets
are validated indexed at load, and the grid road refuses a non-indexed image
outright and points it at the tile pillar. So there is no branch here: unit 0
carries palette indices, unit 1 the tileset's whole bank as a 256x256 table,
and `run_palette_row` says which row to read.

`run_palette_row` is set per RUN; `run_colors` is set once per PASS and
nothing drives it -- see `SpriteBatch.begin`.

Why an RGBA sprite would lose the pillar's colour model, and why the SSBO
path did not ship: `bps/reference/graphics/sprites.md`, "The draw".
"""

from typing import Optional

from pyglet.gl import GL_SAMPLER_BUFFER
from pyglet.graphics.shader import Shader, ShaderProgram

from blitspersecond.common import PACKAGE_ROOT

_SHADERS = PACKAGE_ROOT / "assets" / "shaders"

_program: Optional[ShaderProgram] = None

def _teach_pyglet_sampler_buffer() -> None:
    """Register `GL_SAMPLER_BUFFER` in pyglet's uniform setter table.

    pyglet builds a `_Uniform` for every uniform it introspects and looks the
    GL type up in `_uniform_setters` while doing it. `samplerBuffer` is not in
    that table, so a program containing one raises `KeyError: 36290` **at
    construction** -- not at assignment, which is what makes it look like a
    shader compile failure when it is not. The shader compiles and links
    perfectly well.

    A sampler uniform is an int naming a texture unit whatever it samples, so
    the entry is `GL_SAMPLER_2D`'s, copied. Reaching into another library's
    private table is not free of risk and is done here rather than anywhere
    else so there is one place to find it: if a pyglet upgrade adds the type
    itself, this becomes a no-op rather than a conflict.
    """
    from pyglet.gl import GL_SAMPLER_2D
    from pyglet.graphics.shader import _uniform_setters

    _uniform_setters.setdefault(GL_SAMPLER_BUFFER, _uniform_setters[GL_SAMPLER_2D])


def program() -> ShaderProgram:
    """The sprite pillar's compiled program, built on first use.

    Lazy because compiling needs a current GL context and importing must not:
    a sprite can be constructed before there is a window, and only the draw
    reaches this.
    """
    global _program
    if _program is None:
        _teach_pyglet_sampler_buffer()
        vert = (_SHADERS / "quads.vert").read_text(encoding="utf-8")
        frag = (_SHADERS / "indexed.frag").read_text(encoding="utf-8")
        _program = ShaderProgram(
            Shader(vert, "vertex"),
            Shader(frag, "fragment"),
        )
    return _program
