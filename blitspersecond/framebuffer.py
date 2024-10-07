from .layer import Layer
from numpy import ndarray, zeros, uint8, flip
from pyglet.image import ImageData, Texture

FB_DEPTH = 8
WIDTH = 640
HEIGHT = 360


class FrameBuffer(object):

    _framebuffer = zeros([HEIGHT, WIDTH, 4], dtype=uint8)

    def __init__(self) -> None:
        self._idx = 0
        self._layers = [Layer() for _ in range(FB_DEPTH)]

    def layer(self, index) -> Layer:
        return self._layers[index]

    def _compose(self):
        self._framebuffer.fill(0)
        for layer in self._layers:
            # Access the layer's RGBA image
            layer_image = (
                layer.image
            )  # Assuming `layer.image` returns an (H, W, 4) ndarray

            # Apply layer to framebuffer based on alpha values
            mask_opaque = (
                layer_image[..., 3] > 0x00
            )  # Fully opaque TODO: should be == 0xFF and translucent casse handled
            # mask_transparent = layer_image[..., 3] == 0x00  # Fully transparent
            self._framebuffer[mask_opaque] = layer_image[mask_opaque]

    @property
    def texture(self) -> Texture:
        self._compose()
        return ImageData(
            WIDTH,
            HEIGHT,
            "RGBA",
            flip(self._framebuffer, axis=0).tobytes(),
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
