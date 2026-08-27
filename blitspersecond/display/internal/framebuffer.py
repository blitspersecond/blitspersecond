from typing import TYPE_CHECKING

import pyglet
import pyglet.image.buffer
from pyglet.graphics import shader

from blitspersecond.common import PACKAGE_ROOT

from .viewport import integer_viewport

if TYPE_CHECKING:
    from .surface import Surface


# `blitspersecond.gl.Shader` exists to be SHARED -- tile and sprite each ask
# for a program by name because many layers want the SAME compiled pair.
# `Framebuffer` is constructed exactly once, by `Display.__init__`, for the
# lifetime of the process -- there is no second caller ever asking for
# ("framebuffer", "framebuffer") to share with, so the cache it went through
# bought nothing. Compiled directly, same as the rest of this file's GL
# calls, rather than through a shared cache with exactly one entry.
_SHADERS = PACKAGE_ROOT / "assets" / "shaders"

# The CRT look applied by the post-process pass as it presents the composed
# framebuffer. A fixed house aesthetic, not per-game config -- named here so the
# draw path reads as intent rather than a wall of magic numbers.
_CRT_COLOR_MULTIPLIER = (1.1, 1.05, 0.9)
_CRT_BRIGHTNESS = 1.1
_CRT_CONTRAST = 1.2
_CRT_GAMMA = 0.9

# The one full-screen quad this pass ever draws: two triangles covering clip
# space, textured 0-1. Used to live on `Shader` itself, but this post-pass
# was its only reader anywhere in the tree -- tile/sprite/glyph each build
# their own geometry entirely separately -- so a "what does every shader
# draw" property was really "what does this ONE draw draw".
_QUAD_VERTICES = [-1.0, -1.0, 1.0, -1.0, 1.0, 1.0, -1.0, 1.0]
_QUAD_TEX_COORDS = [0.0, 0.0, 1.0, 0.0, 1.0, 1.0, 0.0, 1.0]
_QUAD_INDICES = [0, 1, 2, 0, 2, 3]


class Framebuffer(pyglet.image.buffer.Framebuffer):
    def __init__(
        self,
        width: int,
        height: int,
    ):
        super().__init__()
        self._color_buffer = pyglet.image.Texture.create(
            width,
            height,
            internalformat=pyglet.gl.GL_RGBA,
            min_filter=pyglet.gl.GL_NEAREST,
            mag_filter=pyglet.gl.GL_NEAREST,
        )
        self.attach_texture(
            self._color_buffer,
            attachment=pyglet.gl.GL_COLOR_ATTACHMENT0,
        )
        self._batch = pyglet.graphics.Batch()

        vert_raw = (_SHADERS / "framebuffer.vert").read_text(encoding="utf-8")
        frag_raw = (_SHADERS / "framebuffer.frag").read_text(encoding="utf-8")
        self._program = shader.ShaderProgram(
            shader.Shader(vert_raw, "vertex"),
            shader.Shader(frag_raw, "fragment"),
        )

    def clear(self, colour: tuple[float, float, float] = (0.0, 0.0, 0.0)) -> None:
        red, green, blue = colour
        self.bind()
        pyglet.gl.glClearColor(red, green, blue, 1.0)
        pyglet.gl.glClear(pyglet.gl.GL_COLOR_BUFFER_BIT)
        self.unbind()

    def draw(self, surface: "Surface") -> None:
        self.unbind()
        w, h = surface.get_size()
        viewport = integer_viewport(self.width, self.height, w, h)

        pyglet.gl.glViewport(0, 0, w, h)
        pyglet.gl.glDisable(pyglet.gl.GL_SCISSOR_TEST)
        pyglet.gl.glClearColor(0.0, 0.0, 0.0, 1.0)
        pyglet.gl.glClear(pyglet.gl.GL_COLOR_BUFFER_BIT)
        pyglet.gl.glViewport(
            viewport.x,
            viewport.y,
            viewport.width,
            viewport.height,
        )

        self._program["texture1"] = 0
        self._program["scaleFactor"] = float(viewport.scale)
        self._program["viewportOriginY"] = float(viewport.y)
        self._program["colorMultiplier"] = _CRT_COLOR_MULTIPLIER
        self._program["brightness"] = _CRT_BRIGHTNESS
        self._program["contrast"] = _CRT_CONTRAST
        self._program["gamma"] = _CRT_GAMMA

        pyglet.gl.glBindTexture(pyglet.gl.GL_TEXTURE_2D, self._color_buffer.id)
        pyglet.gl.glTexParameteri(
            pyglet.gl.GL_TEXTURE_2D,
            pyglet.gl.GL_TEXTURE_MIN_FILTER,
            pyglet.gl.GL_NEAREST,
        )
        pyglet.gl.glTexParameteri(
            pyglet.gl.GL_TEXTURE_2D,
            pyglet.gl.GL_TEXTURE_MAG_FILTER,
            pyglet.gl.GL_NEAREST,
        )
        pyglet.gl.glDisable(pyglet.gl.GL_DEPTH_TEST)
        pyglet.gl.glDisable(pyglet.gl.GL_BLEND)

        self._program.use()
        vertex_list = self._program.vertex_list_indexed(
            count=4,
            mode=pyglet.gl.GL_TRIANGLES,
            indices=_QUAD_INDICES,
            batch=self._batch,
            in_position=("f", _QUAD_VERTICES),
            in_tex_coords=("f", _QUAD_TEX_COORDS),
        )
        vertex_list.draw(pyglet.gl.GL_TRIANGLES)
        vertex_list.delete()
        self._program.stop()


class FramebufferPair:
    """The handoff between a free-running engine and a display on its own grid.

    The engine composes into `next` and calls `complete()`. Display calls
    `promote()` and draws `current`. That is the entire contract between the
    two sides -- they share this buffer pair and nothing else. Neither asks
    the other for permission, which is what keeps a late present from
    becoming a late simulation.

    **Both sides of the rate crossover fall out of it, with no policy code.**
    Engine faster than the panel (60fps on 40Hz): `next` is overwritten
    before it is ever promoted, so the engine decimates by DISCARD -- and
    because the producer never batches, every tick still gets its own event
    pump and input snapshot regardless of what the panel can show. Panel
    faster than the engine (60fps on 144Hz): `next` is not ready, so
    `current` is drawn again. That held-frame re-present is what retires the
    old `recompose` flag; re-showing the last picture stops being a special
    case and becomes the default.

    **Promotion is deliberately late** -- inside the present path, immediately
    before the draw, never at the moment the engine flags completion.
    Promoting on completion would make a finished frame wait for the NEXT
    present rather than the current one, costing up to a full engine frame of
    latency (16.7ms at 60fps). Single-threaded, late promotion is free and
    strictly better. This is invisible until someone measures input lag, so
    it is a rule rather than a preference.

    The second buffer also lets the driver pipeline properly. With one FBO,
    composing frame N+1 writes to a texture that a queued post-process draw
    may still be reading, which the driver resolves by stalling or shadow-
    copying. Alternating gives a full present of separation. About a megabyte
    at 640x360.
    """

    def __init__(self, width: int, height: int) -> None:
        self._next = Framebuffer(width, height)
        self._current = Framebuffer(width, height)
        # Cleared at construction so the first presents show black rather
        # than whatever the driver left in the texture. Presentation starts
        # before the first tick completes -- the clock owes frame one a full
        # step -- so something IS shown before anything is composed.
        self._next.clear()
        self._current.clear()
        self._ready = False
        self._primed = False
        self._dropped = 0
        self._repeated = 0

    @property
    def next(self) -> Framebuffer:
        """The buffer the engine composes into. Never the one being shown."""
        return self._next

    @property
    def dropped(self) -> int:
        """Frames composed but never shown -- `next` overwritten unpromoted.

        The visible artefact of running above the crossover. Expected to be
        zero whenever the panel is faster than the authored rate, so a
        non-zero count on a fast panel is a real finding rather than noise.
        """
        return self._dropped

    @property
    def repeated(self) -> int:
        """Presents that re-showed `current` because no new frame was ready.

        Expected and healthy below the crossover: on a 144Hz panel at 60fps
        roughly three presents in five carry no new content. Counted because
        it is the other half of the same mechanism, not because it is a fault.
        """
        return self._repeated

    def complete(self) -> bool:
        """Engine: the frame in `next` is finished. True if one was dropped.

        A drop means the PREVIOUS frame was never promoted and has just been
        composed over. That is decimation working as designed, but it is
        still a frame the player never saw, so it is reported rather than
        swallowed.
        """
        dropped = self._ready
        if dropped:
            self._dropped += 1
        self._ready = True
        self._primed = True
        return dropped

    def promote(self) -> bool:
        """Display: take the newest finished frame. True if the picture moved.

        Swapping rather than copying means the engine's next compose targets
        the buffer that just stopped being shown -- correct, and free.
        """
        if not self._ready:
            if self._primed:
                self._repeated += 1
            return False
        self._next, self._current = self._current, self._next
        self._ready = False
        return True

    def draw(self, surface: "Surface") -> None:
        """Queue the post-process draw of the frame currently being shown."""
        self._current.draw(surface)
