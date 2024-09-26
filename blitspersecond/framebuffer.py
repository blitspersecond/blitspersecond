import numpy as np
import pyglet
from .layer import Layer
from .config import Config

FRAME_BUFFER_LAYERS = 8


class FrameBuffer:
    _DEPTH = FRAME_BUFFER_LAYERS

    def __init__(self):
        self.height = Config().window.height  # 360, for example
        self.width = Config().window.width  # 640, for example
        self._framebuffer = np.zeros((self.height, self.width, 4), dtype=np.uint8)
        self._layers = [Layer() for _ in range(self._DEPTH)]

    def _compose(self):
        """
        Compose the layers onto the framebuffer, handling transparency.
        Each layer should be composited where alpha > 0.
        """
        self._framebuffer.fill(0)  # Clear the framebuffer

        for layer in self._layers:
            layer_data = layer.image  # Get the image data from the layer
            # Alpha blending: where layer alpha is > 0, blend with framebuffer
            alpha_mask = layer_data[..., 3] > 0  # Mask for pixels with alpha > 0
            self._framebuffer[alpha_mask] = layer_data[alpha_mask]

    @property
    def texture(self):
        self._compose()
        """
        Get the composited framebuffer as a pyglet texture.

        Returns:
            pyglet.image.Texture: The framebuffer as a texture.
        """
        # Correct width and height from framebuffer shape
        return pyglet.image.ImageData(
            self.width,  # Width of the framebuffer
            self.height,  # Height of the framebuffer
            "RGBA",  # Color format
            np.flip(
                self._framebuffer, axis=0
            ).tobytes(),  # Flip vertically for correct orientation
        ).get_texture()

    # Implementing list-like access to the layers
    def __getitem__(self, index):
        """Allows access to individual layers via indexing."""
        if 0 <= index < len(self._layers):
            return self._layers[index]
        raise IndexError(f"Layer index {index} out of bounds.")

    # Implementing the iterable interface
    def __iter__(self):
        """Allows iteration over the layers in the framebuffer."""
        self._current_layer = 0  # Reset iteration
        return self

    def __next__(self):
        """Returns the next layer in the framebuffer during iteration."""
        if self._current_layer >= len(self._layers):
            raise StopIteration
        layer = self._layers[self._current_layer]
        self._current_layer += 1
        return layer


# # Example for more complex blending (alpha compositing):
# alpha_layer = layer_data[..., 3] / 255.0  # Normalize alpha to range [0, 1]
# for c in range(3):  # For each color channel (R, G, B)
#     self._framebuffer[..., c] = (
#         alpha_layer * layer_data[..., c] +
#         (1 - alpha_layer) * self._framebuffer[..., c]
#    )
