import numpy as np
import pyglet
from .layer import Layer
from .config import Config


class FrameBuffer(object):
    _DEPTH = 8

    def __init__(self) -> None:
        self.height = Config().window.height
        self.width = Config().window.width
        self._rgba_framebuffer = np.zeros((self.height, self.width, 4), dtype=np.uint8)
        self._layers = [Layer() for _ in range(self._DEPTH)]

    def _compose(self) -> None:
        """
        Compose the layers onto the framebuffer, handling transparency.
        Each layer should be composited where alpha > 0.
        """
        self._rgba_framebuffer.fill(0)

        for layer in self._layers:
            layer_data = layer.image
            alpha_mask = layer_data[..., 3] > 0
            self._rgba_framebuffer[alpha_mask] = layer_data[alpha_mask]

    @property
    def texture(self) -> pyglet.image.Texture:
        self._compose()
        """
        Get the composited framebuffer as a pyglet texture.

        Returns:
            pyglet.image.Texture: The framebuffer as a texture.
        """
        return pyglet.image.ImageData(
            self.width,
            self.height,
            "RGBA",
            np.flip(self._rgba_framebuffer, axis=0).tobytes(),
        ).get_texture()

    @property
    def depth(self) -> int:
        return self._DEPTH

    @property
    def layer(self, idx: int) -> Layer:
        return self._layers[idx]

    def __getitem__(self, index: int) -> Layer:
        if 0 <= index < len(self._layers):
            return self._layers[index]
        raise IndexError(f"Layer index {index} out of bounds.")

    def __iter__(self):
        self._current_layer = 0
        return self

    def __next__(self) -> Layer:
        """Returns the next layer in the framebuffer during iteration."""
        if self._current_layer >= len(self._layers):
            raise StopIteration
        layer = self._layers[self._current_layer]
        self._current_layer += 1
        return layer


# # Example for more complex blending (alpha compositing):
# alpha_layer = layer_data[..., 3] / 255.0  # Normalize alpha to range [0, 1]
# for c in range(3):  # For each color channel (R, G, B)
#     self._rgba_framebuffer[..., c] = (
#         alpha_layer * layer_data[..., c] +
#         (1 - alpha_layer) * self._rgba_framebuffer[..., c]
#    )
