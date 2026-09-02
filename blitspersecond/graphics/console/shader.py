"""The console's shader program: one whole-buffer quad, palette-indexed.

`picture.vert` + `indexed.frag`. The console composes its whole screen into a
single `PixelBuffer` of palette indices and draws it as one quad wherever it
sits -- no scaling, no rotation, no per-tile anything -- which is exactly what
the picture vertex stage is for. `indexed.frag` then does the palette lookup
on the GPU: unit 0 carries the indices, unit 1 the palette table from
`graphics.internal.PaletteTexture`. A console has one palette, so that table
is a single 256x1 row and the `palette_row` the fragment stage reads is 0.

## Why this is local and not a shared Shader class

There was one, briefly, and before that a whole `blitspersecond.core` package
holding it: a `Shader.get(vert, frag)` keyed cache, so that many drawables
wanting the same compiled pair got one program.

**The engine has five programs in total** -- the framebuffer post-pass, two
for tiles, one for sprites, this one -- and the set is fixed when the engine
is written, not discovered at runtime. There is no material system here and
no intention of one. A keyed cache is the right answer when you cannot know
what will be asked for; when the answer is a five-item list decided in
advance, it is a lookup standing between a reader and a fact they could
otherwise have read.

What it cost was locality. A program is coupled to the draw path that issues
it -- the vertex attributes it expects, the units it samples, what must be
set per draw rather than once -- and none of that could be written next to
the program while the program lived in a shared table keyed by two strings.
Compiling one extra copy of `picture`+`indexed`, because sprites use it too,
is a few milliseconds once per process and a GL object. That is a trivial
price for each engine's shader concerns sitting in the engine.

`Framebuffer` reached this conclusion first and independently: it compiles
inline, having noted that nothing else would ever ask for its pair. The same
reasoning applies to all of them; it was simply most obvious there.

## Shared state, per-draw uniforms

A program has ONE uniform state. Anything per-instance -- the palette row,
any tint -- is set per draw and never once at build, whether or not the
program is shared with anything.
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
