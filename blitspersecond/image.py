import PIL.Image as PillowImage
from numpy import array
from .logger import Logger
from .palette import Palette
from typing import Tuple


class Image:
    def __init__(self, file: str):
        try:
            im = PillowImage.open(file)
        except FileNotFoundError:
            Logger().error(f"File not found: {file}")
            raise FileNotFoundError(f"File not found: {file}")
        if im.mode != "P":
            Logger().error(f"Image is not in palettized (P mode) format: {file}")
            raise ValueError("The image is not in palettized (P mode) format.")

        # Store the image as a numpy array
        self._idx_image = array(im)

        # Initialize the palette
        self._palette = Palette()
        _p = im.getpalette()
        if _p is None:
            Logger().error(f"Palette is not available: {file}")
            raise ValueError("Palette is not available.")
        if len(_p) % 3 != 0:
            Logger().error(f"Palette is not in RGB format: {file}")
            raise ValueError("Palette is not in RGB format.")
        if len(_p) > 16 * 3:
            Logger().error(f"Palette is larger than 16 colors: {file}")
            raise ValueError("Palette is larger than 16 colors.")

        # Populate the palette
        for i in range(0, len(_p), 3):
            r, g, b = _p[i : i + 3]
            self._palette[i // 3] = (r, g, b, 255)

    @property
    def palette(self) -> Palette:
        return self._palette.copy()

    @property
    def image(self) -> array:
        return self._idx_image.copy()

    @property
    def size(self) -> Tuple[int, int]:
        # Return size as (width, height)
        return (self._idx_image.shape[1], self._idx_image.shape[0])
