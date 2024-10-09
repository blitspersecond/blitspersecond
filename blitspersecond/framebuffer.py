from .config import Config
from .numba import numba_compose
from .layer import Layer
from numpy import ndarray, zeros, uint8, flip
from pyglet.image import ImageData, Texture
from numba import njit


from numba import njit, prange


class FrameBuffer(object):
    def __init__(self) -> None:
        c = Config()
        self._width = c.window.width
        self._height = c.window.height
        self._depth = c.framebuffer.depth
        self._framebuffer = zeros([self._height, self._width, 4], dtype=uint8)
        self._idx = 0
        self._layers = [Layer() for _ in range(self._depth)]

    def layer(self, index) -> Layer:
        return self._layers[index]

    def _compose(self):
        layer_images = [layer.image for layer in self._layers]
        self._framebuffer = numba_compose(self._framebuffer, layer_images)

    @property
    def texture(self) -> Texture:
        self._compose()
        return ImageData(
            self._width,
            self._height,
            "RGBA",
            self._framebuffer.tobytes(),
        ).get_texture()

    def __getitem__(self, index: int) -> Layer:
        if 0 <= index < len(self._layers):
            return self._layers[index]
        raise IndexError(f"Layer index {index} out of bounds.")

    def __iter__(self):
        self._idx = 0
        return self

    def __next__(self) -> Layer:
        """Returns the next layer in the framebuffer during iteration."""
        if self._idx >= len(self._layers):
            raise StopIteration
        layer = self._layers[self._idx]
        self._idx += 1
        return layer
