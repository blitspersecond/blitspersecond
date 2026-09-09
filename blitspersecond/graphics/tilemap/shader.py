"""The tilemap's shader programs: many quads, one sheet, one draw.

`default.vert` paired with `indexed.frag` or `direct.frag`. The vertex stage
is shared and used UNMODIFIED: it declares `colors`, `translate`, `scale` and
`rotation` as per-vertex attributes, and the tile batch feeds all four as
*generic* vertex attributes, which a disabled attribute array reads.

`indexed.frag` is the mainline -- unit 0 carries palette indices, unit 1 the
table the map's `PaletteBank` owns and uploads. That table is 256x256 and the
fragment stage reads the row `palette_row` names, which is the bank's `active`
register -- moving it recolours the layer at the next draw. The row is a
register rather than a constant because
the stage is shared with the sprite pillar, where a tileset carries a bank.

`direct.frag` takes an RGBA sheet and samples it straight.

**An RGBA tilemap has no collision, and this is mechanical rather than
policy.** Solidity is derived from palette index 0 meaning transparent, so an
RGBA sheet has nothing to derive a mask from. A tilemap asked for both refuses
at construction rather than carrying an always-empty plane -- do not "fix"
that refusal into a silent fallback. See `bps/reference/tiles.md`, "One bind,
one draw call".

Both programs are module-level and built on first use. Lazy because compiling
needs a current GL context and importing must not: a tilemap can be
constructed before there is a window, and only drawn after there is one.

Why these live beside the code that issues them rather than in a keyed cache
is argued in `graphics/console/shader.py`.
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
    `tileset.buffer.mode` -- so the branch lives here beside the two
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
