from typing import Optional, Tuple

import numpy as np
from PIL import Image as PILImage

from blitspersecond.common import InvalidTransparencyError, Size

from blitspersecond.colors import Palette

_VALID_MODES = ("P", "RGB", "RGBA")


class ImageSpec:
    """A pure description of an image: size, mode, optional pixel data.

    The single source type an Image is built from. data=None means a blank
    (zeroed) canvas; data present means seeded -- from disk (load_image_spec),
    a matplotlib RGBA render, a hand-built array, wherever. No GL, no PIL,
    no cache identity: just a value object.
    """

    def __init__(
        self,
        size: Tuple[int, int],
        mode: str,
        data: Optional[np.ndarray] = None,
        palette: Optional[Palette] = None,
        transparency_index: Optional[int] = None,
    ) -> None:
        if mode not in _VALID_MODES:
            raise ValueError(f"Image mode '{mode}' not supported. Must be one of {_VALID_MODES}.")
        width, height = int(size[0]), int(size[1])
        if width <= 0 or height <= 0:
            raise ValueError(f"Invalid image size {size}.")
        self._size = Size(width, height)
        self._mode = mode
        self._palette = palette
        self._transparency_index = transparency_index

        shape = (height, width) if self.channels == 1 else (height, width, self.channels)
        if data is None:
            data = np.zeros(shape, dtype=np.uint8)
        else:
            data = np.asarray(data, dtype=np.uint8)
            if data.shape != shape:
                raise ValueError(
                    f"data shape {data.shape} does not match expected {shape} "
                    f"for mode '{mode}' at size {self._size}"
                )
        self._data = data

    @property
    def size(self) -> Size:
        return self._size

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def fmt(self) -> str:
        """The pyglet ImageData format string. Indexed images upload their
        palette indices as a single-channel R8 texture; the INDEXED shader
        does the palette lookup on the GPU."""
        return "R" if self._mode == "P" else self._mode

    @property
    def channels(self) -> int:
        return 1 if self._mode == "P" else len(self._mode)

    @property
    def data(self) -> np.ndarray:
        return self._data

    @property
    def palette(self) -> Optional[Palette]:
        return self._palette

    @property
    def transparency_index(self) -> Optional[int]:
        return self._transparency_index

    @property
    def indexed(self) -> bool:
        return self._mode == "P"


def load_image_spec(filename: str) -> ImageSpec:
    """Load an image file into an ImageSpec (the loader step; PIL lives here).

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

        transparency_index = None
        if "transparency" in img.info:
            transparency_index = img.info["transparency"]
            if transparency_index != 0:
                raise InvalidTransparencyError(
                    f"Image {filename} has transparency at index {transparency_index}, "
                    f"but BPS requires index 0."
                )

        # Row 0 = top (PIL order). Uploaded as-is: content stays GL
        # upside-down everywhere; the framebuffer flips once before display.
        data = np.asarray(img, dtype=np.uint8)

        return ImageSpec(
            size=(int(img.size[0]), int(img.size[1])),
            mode=img.mode,
            data=data,
            palette=palette,
            transparency_index=transparency_index,
        )
    except InvalidTransparencyError as e:
        raise InvalidTransparencyError(
            f"{e}\n If this annoys you enough, make a CLI tool to fix it."
        ) from e
