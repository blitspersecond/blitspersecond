# Palette RAM made literal: a Palette's GPU projection is a 256x1 RGBA
# texture the INDEXED shader samples (unit 1), not a 256-vec4 uniform. The
# uniform cost the engines ~0.15ms PER INDEXED DRAW, every compose, because
# the shared program has one uniform state and alternating layers stomp each
# other's table; a texture bind is nanoseconds and the upload happens only
# when the palette actually mutated. This is also the doorway to the sprite
# engine's palette-bank future ("palette as a vertex attribute"): once the
# table is samplable, WHICH table can become per-quad data.

from __future__ import annotations

from weakref import WeakKeyDictionary

from pyglet.gl import GL_NEAREST
from pyglet.image import ImageData, Texture

from blitspersecond.resources import Palette


class _Entry:
    __slots__ = ("texture", "version")

    def __init__(self, texture: Texture) -> None:
        self.texture = texture
        self.version = -1  # forces the first upload


class PaletteTexture:
    """Memo: Palette identity -> its 256x1 texture, upload gated by the
    palette's mutation counter. Weak keys so a palette that dies releases
    its texture entry; identity (not equality) is the key on purpose --
    sharing a Palette object IS sharing its GPU table, same law as
    PixelBuffer sharing."""

    _cache: "WeakKeyDictionary[Palette, _Entry]" = WeakKeyDictionary()

    @classmethod
    def get(cls, palette: Palette) -> Texture:
        """The palette's texture, current as of its last mutation. Needs a
        GL context (draw-time machinery, like PixelBuffer.texture)."""
        entry = cls._cache.get(palette)
        if entry is None:
            entry = _Entry(
                Texture.create(
                    256,
                    1,
                    min_filter=GL_NEAREST,
                    mag_filter=GL_NEAREST,
                )
            )
            cls._cache[palette] = entry
        if entry.version != palette.version:
            entry.texture.blit_into(
                ImageData(width=256, height=1, fmt="RGBA", data=palette.tobytes(), pitch=None),
                0,
                0,
                0,
            )
            entry.version = palette.version
        return entry.texture
