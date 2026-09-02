"""The tilemap's shader programs: many quads, one sheet, one draw.

`default.vert` paired with `indexed.frag` or `direct.frag` -- two of the five
programs the engine has in total. The vertex stage is shared and used
UNMODIFIED: it declares `colors`, `translate`, `scale` and `rotation` as
per-vertex attributes, and the tile batch feeds all four as *generic* vertex
attributes (`glVertexAttrib*f`, set per draw, no buffers), which a disabled
attribute array reads. So a tilemap never scales or rotates a quad, and it
pays nothing to say so.

Why these live beside the code that issues them, rather than in a keyed
`Shader.get(vert, frag)` cache, is argued at length in
`graphics/console/shader.py`. The same reasoning applies here and is not
repeated.

## Two programs, because a tileset either is indexed or it is not

`indexed.frag` is the mainline -- the sheet holds palette indices, unit 0
carries them and unit 1 the palette table from
`graphics.internal.PaletteTexture`. That table is 256xN and the fragment
stage reads the row `palette_row` names; a tilemap draws through one sheet
with one palette, so N is 1 and the row is 0, set once as a constant generic
attribute (see `batch.py`). The row exists because the fragment stage is
shared with the sprite pillar, where a tileset carries a bank.

`direct.frag` takes an RGBA sheet and samples it straight. **RGBA is supported
on principle**: if the hardware can do it, a game should be able to reach it,
and refusing in order to look more retro would be affectation rather than
design.

Both multiply by `vertex_colors`, so the whole-layer tint and fade registers
behave identically in either mode.

**One constraint rides with RGBA, and it is mechanical rather than policy: an
RGBA tilemap has no collision at all.** Solidity is derived from palette index
0 meaning transparent, which is what the alpha mask keys on. An RGBA sheet has
no palette, so there is no index 0 and nothing to derive a mask from. It is
unbuildable, not switched off -- which is why a tilemap asked for both refuses
at construction rather than quietly carrying an always-empty mask.

That trade costs nothing anyone wanted. RGBA is what a game reaches for on
fog, cloud translucency and high-colour backdrops: scenery, which nothing asks
collision questions about. Those are layers spent for drawing; a playfield
that must answer `collides_with` is a different layer.

## Compiled once per process

Both programs are module-level and built on first use. A tilemap holds no
reference to its program and no per-instance shader state -- it asks for the
one that exists, at draw time, exactly as `Console` does. Lazy because
compiling needs a current GL context and importing must not: a tilemap can be
constructed before there is a window, and only drawn after there is one.
"""

from typing import Optional

from pyglet.graphics.shader import Shader, ShaderProgram

from blitspersecond.common import PACKAGE_ROOT

#: Resolved against the PACKAGE, not the working directory: these are engine
#: assets shipped inside it, so a game that `pip install -e`'d the engine and
#: runs from its own directory still finds them.
_SHADERS = PACKAGE_ROOT / "assets" / "shaders"

_indexed: Optional[ShaderProgram] = None
_direct: Optional[ShaderProgram] = None


def _build(fragment: str) -> ShaderProgram:
    """Compile `default.vert` against one of the two fragment stages."""
    vertex = (_SHADERS / "default.vert").read_text(encoding="utf-8")
    shading = (_SHADERS / fragment).read_text(encoding="utf-8")
    return ShaderProgram(
        Shader(vertex, "vertex"),
        Shader(shading, "fragment"),
    )


def program(indexed: bool) -> ShaderProgram:
    """The program for an indexed or an RGBA sheet, built on first use.

    `indexed` is the question a tilemap already has the answer to --
    `tileset.indexed` -- so the branch lives here beside the two programs
    being chosen between, rather than at each draw site.

    Deliberately two named globals and not a cache keyed on the shader pair.
    The engine's program set is a five-item list decided when the engine is
    written; a lookup would stand between a reader and a fact they could
    otherwise read.
    """
    global _indexed, _direct
    if indexed:
        if _indexed is None:
            _indexed = _build("indexed.frag")
        return _indexed
    if _direct is None:
        _direct = _build("direct.frag")
    return _direct
