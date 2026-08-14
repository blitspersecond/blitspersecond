from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Tuple

from pyglet.graphics import shader

from blitspersecond.resources import ResourceManager

# The engine's own shader sources, resolved against THIS FILE rather than the
# process's working directory. They used to be fetched as the literal relative
# path "blitspersecond/assets/shaders/...", which silently required every game
# to be launched from the engine repo root -- fine while the engine was only
# ever run in-tree, and an immediate FileNotFoundError the moment a game
# installed it (`pip install -e`) and ran from its own directory. These are
# ENGINE assets, shipped inside the package, so the package is what they hang
# off. (internal -> graphics -> blitspersecond)
_SHADERS = Path(__file__).resolve().parents[2] / "assets" / "shaders"


class Shader:
    """A compiled program named by its (vert, frag) source pair.

    Which programs exist is the ENGINES' business, not a colour-mode table: the
    tile engine asks for ("default", "indexed") -- palettised only -- the picture
    engine for ("static", "direct"/"indexed"), the framebuffer post-pass for
    ("framebuffer", "framebuffer"). This class only fetches, compiles and
    caches.
    """

    # Shared program cache keyed on the source pair: drawables share one
    # compiled program per pair instead of each compiling their own. NOTE: a
    # shared program has one uniform state, so per-instance uniforms (e.g. an
    # indexed palette) must be set per draw, not once at build.
    _cache: Dict[Tuple[str, str], "Shader"] = {}

    @classmethod
    def get(cls, vert: str, frag: str) -> "Shader":
        key = (vert, frag)
        if key not in cls._cache:
            cls._cache[key] = cls(vert, frag)
        return cls._cache[key]

    def __init__(self, vert: str, frag: str):
        self._rm = ResourceManager()

        vert_raw = self._rm.fetch(str(_SHADERS / f"{vert}.vert")).content
        frag_raw = self._rm.fetch(str(_SHADERS / f"{frag}.frag")).content

        # Compile the Shader
        frag_shader = shader.Shader(frag_raw, "fragment")
        vert_shader = shader.Shader(vert_raw, "vertex")

        self._program = shader.ShaderProgram(frag_shader, vert_shader)
        self._uniforms: Dict[str, int] = {}

    @property
    def vertices(self) -> List[float]:
        return [-1.0, -1.0, 1.0, -1.0, 1.0, 1.0, -1.0, 1.0]

    @property
    def tex_coords(self) -> List[float]:
        return [0.0, 0.0, 1.0, 0.0, 1.0, 1.0, 0.0, 1.0]

    @property
    def indices(self) -> List[int]:
        return [0, 1, 2, 0, 2, 3]

    @property
    def program(self) -> shader.ShaderProgram:
        return self._program
