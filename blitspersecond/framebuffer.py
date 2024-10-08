from .layer import Layer
from numpy import ndarray, zeros, uint8, flip
from pyglet.image import ImageData, Texture
from numba import njit

FB_DEPTH = 8
WIDTH = 640
HEIGHT = 360

from numba import njit, prange


@njit(parallel=True)
def compose_layers(framebuffer: ndarray, layers) -> ndarray:
    framebuffer.fill(0)  # Clear the framebuffer at the beginning

    for layer in layers:
        # Assuming layer_image is an (H, W, 4) ndarray
        height, width = layer.shape[:2]

        # Loop over each pixel in the layer
        for y in prange(height):
            for x in range(width):
                alpha = layer[y, x, 3]

                # Only apply if pixel is not fully transparent (alpha > 0)
                if alpha > 0:
                    framebuffer[y, x, :] = layer[y, x, :]

    return framebuffer


class FrameBuffer(object):

    _framebuffer = zeros([HEIGHT, WIDTH, 4], dtype=uint8)

    def __init__(self) -> None:
        self._idx = 0
        self._layers = [Layer() for _ in range(FB_DEPTH)]

    def layer(self, index) -> Layer:
        return self._layers[index]

    def _compose(self):
        layer_images = [layer.image for layer in self._layers]
        self._framebuffer = compose_layers(self._framebuffer, layer_images)
        # for layer in self._layers:
        #     # Access the layer's RGBA image
        #     layer_image = (
        #         layer.image
        #     )  # Assuming `layer.image` returns an (H, W, 4) ndarray

        #     # Apply layer to framebuffer based on alpha values
        #     mask_opaque = (
        #         layer_image[..., 3] > 0x00
        #     )  # Fully opaque TODO: should be == 0xFF and translucent case handled
        #     # mask_transparent = layer_image[..., 3] == 0x00  # Fully transparent
        #     self._framebuffer[mask_opaque] = layer_image[mask_opaque]

    @property
    def texture(self) -> Texture:
        self._compose()
        # self._framebuffer[:, ::-1, :] = self._framebuffer[:, ::-1, :]
        return ImageData(
            WIDTH,
            HEIGHT,
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
