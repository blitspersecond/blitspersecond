import itertools
from contextlib import contextmanager
from typing import Iterator, Optional

import numpy as np
from pyglet.gl import GL_NEAREST
from pyglet.image import ImageData, Texture

from blitspersecond.common import Size
from blitspersecond.resources import ImageSpec, Palette


class PixelBuffer:
    """The pixel data primitive -- owns one image's full data lifecycle in both
    system RAM and GPU RAM. Knows nothing about drawing, quads, or tiling.

    Built from an ImageSpec (the factory that carries size/depth/initial data);
    copies the spec's data out so the shared spec stays pristine. Identity =
    sharing: hand the SAME PixelBuffer to N views/layers and they share one
    texture on the GPU.

    A blank canvas or a text console is just a PixelBuffer -- no file. To draw
    one, hand it to layers.add (auto-wrapped: one whole-buffer tile) or make a
    layer yourself: TileEngine(buffer, tilewidth, tileheight).

    The numpy buffer is the source of truth; the texture is a lazy, dirty-gated
    projection of it (created on first .texture access, re-uploaded only when
    .dirty). Never read back from the GPU.
    """

    # Monotonic, process-wide atlas ids -- a Tile carries one to name the texture
    # it samples, and the TileCache groups tiles by it (see .tile / reclaim).
    _ids = itertools.count()

    def __init__(self, spec: ImageSpec) -> None:
        if not isinstance(spec, ImageSpec):
            raise TypeError(f"expected ImageSpec, got {type(spec)}")
        self._mode = spec.mode
        self._fmt = spec.fmt
        self._size = spec.size
        self._palette = spec.palette
        self._transparency_index = spec.transparency_index
        # Own copy -- breaks the reference to the spec's (shared) buffer.
        self._pixels: np.ndarray = np.array(spec.data)
        self._texture: Optional[Texture] = None
        self._dirty: bool = False
        # Collision mask (source space, indexed surfaces only): (h, w) bool,
        # lazily built like .texture and rebuilt only when pixels changed
        # since the last read. Separate flag from _dirty because the two
        # caches (GPU texture, CPU mask) are cleared by different readers at
        # different times -- texture on .texture access, mask on .mask
        # access -- and one reader's read must not hide staleness from the
        # other's.
        self._mask: Optional[np.ndarray] = None
        self._mask_dirty: bool = True
        # Stable identity: shared by every view/layer that reuses this buffer,
        # and the key its tiles live under in the global TileCache. The cache
        # anchors tile reclaim on this buffer's lifecycle itself (a finalize
        # registered at first intern) -- the buffer knows nothing about tiles.
        self._id: int = next(PixelBuffer._ids)

    # -- pixels / texture ------------------------------------------------

    @property
    def pixels(self) -> np.ndarray:
        """The mutable CPU pixel buffer (row 0 = top). numpy writes can't be
        observed, so set .dirty = True after drawing (or use .edit())."""
        return self._pixels

    @property
    def dirty(self) -> bool:
        return self._dirty

    @dirty.setter
    def dirty(self, value: bool) -> None:
        self._dirty = bool(value)
        if self._dirty:
            self._mask_dirty = True

    @contextmanager
    def edit(self) -> Iterator[np.ndarray]:
        """Draw inside `with buffer.edit() as px:` -- marks dirty on exit."""
        try:
            yield self._pixels
        finally:
            self.dirty = True

    @property
    def texture(self) -> Texture:
        """This buffer's GPU texture. Lazily created on first access (needs a
        current GL context); re-uploads the CPU buffer iff dirty."""
        if self._texture is None:
            width, height = self._size
            self._texture = Texture.create(
                width,
                height,
                min_filter=GL_NEAREST,
                mag_filter=GL_NEAREST,
            )
            self._upload()
        elif self._dirty:
            self._upload()
        return self._texture

    def _upload(self) -> None:
        """One-directional: CPU buffer -> GPU. .tobytes() only happens here,
        gated by dirty -- once per change, not per frame."""
        width, height = self._size
        data = ImageData(
            width=width,
            height=height,
            fmt=self._fmt,
            data=np.ascontiguousarray(self._pixels).tobytes(),
            pitch=None,
        )
        assert self._texture is not None
        self._texture.blit_into(data, 0, 0, 0)
        self._dirty = False

    # -- identity / metadata ---------------------------------------------

    @property
    def id(self) -> int:
        """Stable atlas id -- names this buffer's texture in a Tile and keys its
        pool in the TileCache. Shared by every view that reuses this buffer."""
        return self._id

    @property
    def size(self) -> Size:
        return self._size

    @property
    def width(self) -> int:
        return self._size[0]

    @property
    def height(self) -> int:
        return self._size[1]

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def fmt(self) -> str:
        return self._fmt

    @property
    def indexed(self) -> bool:
        return self._mode == "P"

    @property
    def transparency_index(self) -> Optional[int]:
        return self._transparency_index

    @property
    def palette(self) -> Optional[Palette]:
        # The stored instance (None if this buffer isn't indexed), never a fresh
        # copy. _composite() reads this every frame per layer -- but only when
        # `indexed` -- so a rebuilt throwaway would defeat PaletteTexture's
        # identity memo (and once cost ~2.4ms/call rebuilding the table).
        # Same instance -> same GPU palette row.
        return self._palette

    @palette.setter
    def palette(self, value: Palette) -> None:
        """Replace an indexed buffer's live palette without touching pixels."""
        if not self.indexed:
            raise ValueError("only an indexed PixelBuffer has a palette")
        if not isinstance(value, Palette):
            raise TypeError(f"expected Palette, got {type(value)}")
        self._palette = value

    @property
    def mask(self) -> np.ndarray:
        """(height, width) bool array, source space: True where the pixel is
        collidable.

        Indexed surfaces only -- keyed off the palette's alpha channel via
        the raw index, since that's cheaper than resolving each pixel through
        the palette: index 0 is always fully transparent (Palette enforces
        this at construction/write time), every other index opaque. Lazily
        built and cached like .texture, rebuilt only when pixels changed
        since the last read."""
        if not self.indexed:
            raise ValueError(f"mask requires an indexed (palette) surface, got mode {self._mode!r}")
        if self._mask is None or self._mask_dirty:
            self._mask = self._pixels != 0
            self._mask_dirty = False
        return self._mask
