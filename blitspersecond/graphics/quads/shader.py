"""The quad pillar's indexed and direct shader programs, built on first use."""

from typing import Optional

from pyglet.graphics.shader import Shader, ShaderProgram

from blitspersecond.common import PACKAGE_ROOT

_SHADERS = PACKAGE_ROOT / "assets" / "shaders"
_indexed: Optional[ShaderProgram] = None
_direct: Optional[ShaderProgram] = None


def _build(fragment: str) -> ShaderProgram:
    vertex = (_SHADERS / "quad.vert").read_text(encoding="utf-8")
    shading = (_SHADERS / fragment).read_text(encoding="utf-8")
    return ShaderProgram(Shader(vertex, "vertex"), Shader(shading, "fragment"))


def program(indexed: bool) -> ShaderProgram:
    """The program for an indexed or direct-colour atlas."""
    global _indexed, _direct
    if indexed:
        if _indexed is None:
            _indexed = _build("quad_indexed.frag")
        return _indexed
    if _direct is None:
        _direct = _build("quad_direct.frag")
    return _direct
