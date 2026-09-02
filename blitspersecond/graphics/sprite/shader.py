"""The sprite pillar's shader program: many quads, many materials, one pass.

`quads.vert` + `indexed.frag`. The vertex stage is this pillar's OWN as of
2026-09-02: it reads no vertex attributes at all and rebuilds each quad from
a buffer indexed by `gl_VertexID / 4` -- a shader storage buffer where the
driver has one, a buffer texture otherwise. See `quad_storage`. The tilemap
and console keep `default.vert`.

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

## What is per draw here

`run_colors` and `run_palette_row` are uniforms set per RUN. They were generic
vertex attributes until this pillar stopped having vertex attributes at all;
same shape, same cost, one ctypes call each.

Only the row is really per run. Colour belongs to the SPRITE since the tier
collapse, so a chain carries one value and cannot cut a run with it -- see
`Presentation.red`. The row can: a costume swap between two presentations used
to be a second texture on unit 1 and is a number now, because the bank they
both belong to is one table.
"""

import os
from typing import Optional

from pyglet.gl import GL_SAMPLER_BUFFER, gl_info
from pyglet.graphics.shader import Shader, ShaderProgram

from blitspersecond.common import PACKAGE_ROOT

_SHADERS = PACKAGE_ROOT / "assets" / "shaders"

_program: Optional[ShaderProgram] = None

#: Forced path. `None` asks the driver. Seeded from `BPS_QUAD_STORAGE` so the
#: whole suite can be run either way on one machine --
#: `BPS_QUAD_STORAGE=texture pytest` -- which is the only thing that stops the
#: path this box does not use from rotting.
_forced: Optional[str] = None


#: What `BPS_QUAD_STORAGE` and `force_quad_storage` accept.
QUAD_STORAGE = ("ssbo", "texture")

#: The answer, once. Cached because it is asked TWICE -- when the program is
#: built and when the batch is built -- and those two must agree: the path is
#: compiled into the program and bound by the batch, so a driver query that
#: returned differently on the second call would leave a program reading an
#: SSBO while the batch filled a texture, with nothing raising.
_storage: Optional[str] = None


def _from_environment() -> Optional[str]:
    """`BPS_QUAD_STORAGE`, checked here rather than trusted downstream.

    A typo would otherwise surface as a `KeyError` on a dict of store classes
    in the batch, several frames from the environment variable that caused it
    and after a program has already been built the other way.
    """
    kind = os.environ.get("BPS_QUAD_STORAGE") or None
    if kind is not None and kind not in QUAD_STORAGE:
        raise ValueError(
            f"BPS_QUAD_STORAGE={kind!r} is not a quad storage path -- it is "
            f"one of {', '.join(QUAD_STORAGE)}, or unset to take whatever the "
            f"driver supports"
        )
    return kind


def quad_storage() -> str:
    """Which way the vertex stage reaches the quad records: `"ssbo"` or
    `"texture"`.

    A shader storage buffer where the driver has one and a buffer texture
    otherwise, decided once and reported so a caller can say which it got.
    The SSBO is measurably better -- 15.42ms against 15.96ms at 250,000, and a
    materially better p99 -- and it needs GL 4.3, which is above where every
    other shader in this engine sits and above macOS, which stops at 4.1.

    **Both paths run the same geometry.** `quads.vert` forks at the fetch and
    nowhere else, so the corner expansion and the turn permutation exist once.
    What a second path really costs is that only one of them runs on any given
    machine, so the other rots -- which is why `tests/test_indexed_draw.py`
    forces each in turn rather than testing whatever this box happens to
    support.
    """
    global _storage
    if _storage is None:
        kind = _forced if _forced is not None else _from_environment()
        _storage = kind or ("ssbo" if gl_info.have_version(4, 3) else "texture")
    return _storage


def force_quad_storage(kind: Optional[str]) -> None:
    """Pin the storage path, or `None` to ask the driver again.

    For tests, and it rebuilds the program: the choice is compiled in, so
    changing it after the fact would leave a program that disagrees with the
    binding the batch is about to make.
    """
    global _forced, _program, _storage
    if kind is not None and kind not in QUAD_STORAGE:
        raise ValueError(
            f"no such quad storage: {kind!r} -- it is one of "
            f"{', '.join(QUAD_STORAGE)}, or None to ask the driver"
        )
    _forced = kind
    _storage = None
    _program = None


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
        # The version and the path are prepended rather than written into the
        # file, because they are the only two things that differ and a second
        # copy of the file would be a second copy of the geometry.
        storage = quad_storage()
        header = (
            "#version 430\n#define BPS_QUAD_SSBO 1\n"
            if storage == "ssbo"
            else "#version 330\n"
        )
        vert = header + (_SHADERS / "quads.vert").read_text(encoding="utf-8")
        frag = (_SHADERS / "indexed.frag").read_text(encoding="utf-8")
        _program = ShaderProgram(
            Shader(vert, "vertex"),
            Shader(frag, "fragment"),
        )
    return _program
