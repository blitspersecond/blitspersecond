import itertools
from contextlib import contextmanager
from typing import Iterator, Optional, Sequence, Union

import numpy as np
from PIL import Image as PILImage
from pyglet.gl import GL_NEAREST
from pyglet.image import ImageData, Texture

from blitspersecond.common import (
    CacheableResource,
    InvalidTransparencyError,
    Size,
)

from .palette import Palette, system_palette

_VALID_MODES = ("P", "RGB", "RGBA")


class PixelBuffer(CacheableResource):
    """The pixel primitive: one picture, in system RAM and on the GPU.

    Two roads into one constructor, told apart by the first argument:

        art   = PixelBuffer("island.png")            # from disk
        blank = PixelBuffer((64, 64), "P")           # empty canvas
        atlas = PixelBuffer((w, h), "RGBA", pixels)  # from an array

    **Naming a file gives you THE buffer for that file.** Two layers over one
    tileset, or a map and a hand-placed layer over the same sheet, land on one
    texture and one tile pool rather than silently duplicating both:

        PixelBuffer("island.png") is PixelBuffer("island.png")   # True

    That is the point of the type. `id` names this buffer's texture in every
    tile that samples it, so identity is not an optimisation laid over the
    design, it IS the design. Building a second one over the same art is
    something you have to ASK for -- `copy()` -- because it costs a second
    upload and a second pool, and because wanting it is rare and specific
    (destructive edits: eating the dirt in a Boulder Dash).

    A size instead of a path is never shared: that is a canvas you asked to
    have made, and there is no file for two callers to have meant the same
    one. A console screen and a scratch surface are just this.

    The numpy array is the source of truth; the texture is a lazy, dirty-gated
    projection of it -- created on first `.texture` access, re-uploaded only
    when `.dirty`. Nothing is ever read back from the GPU, and a buffer that
    is never drawn never touches GL at all, so one can be built before there
    is a window.

    THERE IS NO SEPARATE `Image` TYPE, and briefly there was. A value object
    holding size/mode/pixels/palette sat underneath this one, mirroring
    reference's `ImageSpec`, and every one of its members existed here too --
    six of them, with the array itself under two names. The split was
    inherited rather than reasoned: `ImageSpec` earned its name by being a
    shared, pristine description that `PixelBuffer` copied out of, and once
    the sharing moved here that description was just a picture with a texture
    missing. Loading a picture and having a picture are one act.
    """

    # Monotonic, process-wide ids, for telling two buffers apart in a repr.
    # This used to name a buffer's texture inside a Tile and key its pool in
    # the TileCache; a tile now names its map and there is no pool, so what is
    # left is a debugging aid. See `id` below.
    _ids = itertools.count()

    @classmethod
    def _cache_key(
        cls,
        source: Union[str, Sequence[int]],
        mode: Optional[str] = None,
        data: Optional[np.ndarray] = None,
        palette: Optional[Palette] = None,
    ) -> Optional[str]:
        # A file has identity; a canvas does not. Two callers naming one path
        # mean the same art and should share its texture. Two callers asking
        # for a 64x64 surface mean two surfaces.
        return cls._path_key(source) if isinstance(source, str) else None

    def _load(
        self,
        source: Union[str, Sequence[int]],
        mode: Optional[str] = None,
        data: Optional[np.ndarray] = None,
        palette: Optional[Palette] = None,
    ) -> None:
        if isinstance(source, str):
            if mode is not None or data is not None or palette is not None:
                raise TypeError(
                    "PixelBuffer(path) reads mode, pixels and palette from "
                    "the file -- pass a size instead to build one by hand"
                )
            size, mode, data, palette = _read(source)
        else:
            size = source
            if mode is None:
                raise TypeError(
                    "PixelBuffer(size, mode) needs a mode: P, RGB or RGBA"
                )

        if mode not in _VALID_MODES:
            raise ValueError(
                f"Image mode '{mode}' not supported. Must be one of {_VALID_MODES}."
            )
        width, height = int(size[0]), int(size[1])
        if width <= 0 or height <= 0:
            raise ValueError(f"Invalid image size {size}.")

        self._size = Size(width, height)
        self._mode = mode
        self._fmt = "R" if mode == "P" else mode
        # An indexed buffer ALWAYS has a table. A blank canvas gets the system
        # palette rather than None, so a surface you just made can be written
        # in real, named colours (`px[...] = RED`) without first building a
        # palette to make the indices mean anything. A file brings its own.
        #
        # A fresh instance per buffer, not a shared one: a palette is mutable
        # per-buffer state, and `PaletteTexture` keys its GPU row on palette
        # IDENTITY -- so sharing one object would silently share one row, and
        # recolouring one surface would recolour every other blank one.
        self._palette = palette
        if mode == "P" and self._palette is None:
            self._palette = system_palette()

        channels = 1 if mode == "P" else len(mode)
        shape = (height, width) if channels == 1 else (height, width, channels)
        if data is None:
            data = np.zeros(shape, dtype=np.uint8)
        else:
            # Copied, never adopted: an array handed in stays the caller's,
            # and writes to it must not turn up on screen behind `dirty`.
            data = np.array(data, dtype=np.uint8)
            if data.shape != shape:
                raise ValueError(
                    f"data shape {data.shape} does not match expected {shape} "
                    f"for mode '{mode}' at size {self._size}"
                )
        self._pixels: np.ndarray = data

        self._texture: Optional[Texture] = None
        self._dirty: bool = False
        # Collision mask (source space, indexed surfaces only): (h, w) bool,
        # lazily built like .texture and rebuilt only when pixels changed
        # since the last read. Separate flag from _dirty because the two
        # caches (GPU texture, CPU mask) are cleared by different readers at
        # different times -- texture on .texture access, mask on .mask access
        # -- and one reader's read must not hide staleness from the other's.
        self._mask: Optional[np.ndarray] = None
        self._mask_dirty: bool = True
        self._id: int = next(PixelBuffer._ids)

    # -- pixels / texture ------------------------------------------------

    @property
    def pixels(self) -> np.ndarray:
        """The mutable CPU pixel buffer (row 0 = top).

        Numpy writes cannot be observed, so set `.dirty = True` when you have
        finished drawing, or use `edit()`, which does it for you. Forgetting
        is the classic version of this bug: the pixels are right, the texture
        is old, and nothing anywhere reports a problem.

        SHARED if this buffer came from a file -- that is what naming a path
        gets you -- so painting here reaches every layer drawing from it. That
        is usually the intent; `copy()` is how to get a surface of your own.

        For an indexed buffer these are palette INDICES, so writing a colour
        does not do what it looks like. Recolouring is a palette job: the
        indices stay where they are and the shader looks up something else.
        """
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
        """One-directional: CPU buffer -> GPU. `.tobytes()` only happens here,
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

    def copy(self) -> "PixelBuffer":
        """An independent buffer with the same pixels and a NEW identity.

        The only way to own art that arrived shared. The twin gets its own
        `id`, its own lazily created texture, its own collision mask and its
        own palette; writing to it reaches nothing else, and it is not in the
        shared table, so nobody else can reach it either.

        This is what a destructible surface is built on, and why that happens
        at buffer granularity rather than at an atlas above it: an atlas is
        arithmetic OVER a buffer and would still share the pixels.
        """
        # The palette is rebuilt rather than passed: this constructor stores
        # what it is handed, so sharing ours would leave the twin recolouring
        # us. Same law a sprite sheet applies per sheet.
        palette = Palette(self._palette.raw()) if self._palette else None
        return PixelBuffer(self._size, self._mode, self._pixels, palette)

    # -- identity / metadata ---------------------------------------------

    @property
    def id(self) -> int:
        """Stable id, for telling two buffers apart when reading a repr or a
        log. Shared by every layer that reuses this buffer -- naming one path
        twice gets one buffer and one id -- and different in every `copy()`.

        It had a load-bearing job once: a `Tile` carried it to name the texture
        it sampled, and `TileCache` keyed its pool on it. A tile names its
        owning map now and there is no pool, so nothing in the engine reads
        this but `__repr__`. Kept because buffer identity is a real question a
        person debugging asks; go through `is` in code."""
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
        """The pyglet ImageData format string. Indexed buffers upload their
        palette indices as a single-channel R8 texture; the INDEXED shader
        does the palette lookup on the GPU."""
        return self._fmt

    @property
    def channels(self) -> int:
        return 1 if self._mode == "P" else len(self._mode)

    @property
    def indexed(self) -> bool:
        return self._mode == "P"

    @property
    def palette(self) -> Optional[Palette]:
        # The stored instance (None exactly when this buffer isn't indexed --
        # an indexed one always has a table, defaulting to `system_palette()`),
        # never a fresh copy. The compositor reads this every frame per layer -- but
        # only when `indexed` -- so a rebuilt throwaway would defeat
        # PaletteTexture's identity memo, and once cost ~2.4ms/call
        # rebuilding the table. Same instance -> same GPU palette row.
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

        Indexed surfaces only -- keyed off the raw index rather than resolved
        colour, since index 0 is always fully transparent (`Palette` enforces
        that at construction and at every write) and every other index is
        opaque. That makes the mask a comparison against zero instead of 256
        palette lookups. Lazily built and cached like `.texture`, rebuilt only
        when pixels changed since the last read.
        """
        if not self.indexed:
            raise ValueError(
                f"mask requires an indexed (palette) surface, got mode {self._mode!r}"
            )
        if self._mask is None or self._mask_dirty:
            self._mask = self._pixels != 0
            self._mask_dirty = False
        assert self._mask is not None
        return self._mask

    def __repr__(self) -> str:
        return f"<PixelBuffer #{self._id} {self.width}x{self.height} {self._mode}>"


def _read(filename: str):
    """The disk half: PIL lives here and nowhere else in the engine.

    Validates mode (P/RGB/RGBA) and enforces the engine convention that
    indexed transparency sits at palette index 0.
    """
    try:
        img = PILImage.open(filename)
        if img.mode not in _VALID_MODES:
            raise ValueError(
                f"Image mode '{img.mode}' not supported. Must be P, RGB, or RGBA."
            )

        palette = None
        if img.mode == "P":
            palette_list = img.getpalette()
            if palette_list is not None:
                palette = Palette(np.array(palette_list, dtype=np.uint8).tolist())

        # Index 0 is the engine-wide hole: Palette pins it to alpha 0, and
        # masks and collision solidity key off the index rather than the
        # alpha. A file that puts its hole anywhere else would be silently
        # solid there, so it is refused rather than carried. Checked, not
        # kept -- there is nothing downstream that could act on the value.
        if img.info.get("transparency", 0) != 0:
            raise InvalidTransparencyError(
                f"Image {filename} has transparency at index "
                f"{img.info['transparency']}, but BPS requires index 0."
            )

        # Row 0 = top (PIL order). Uploaded as-is: content stays GL
        # upside-down everywhere; the framebuffer flips once before display.
        data = np.asarray(img, dtype=np.uint8)
        return (int(img.size[0]), int(img.size[1])), img.mode, data, palette
    except InvalidTransparencyError as e:
        raise InvalidTransparencyError(
            f"{e}\n If this annoys you enough, make a CLI tool to fix it."
        ) from e
