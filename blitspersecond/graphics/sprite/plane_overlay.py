from typing import Dict, Optional, Tuple

from blitspersecond.colors import ConsolePalette
from blitspersecond.graphics.common import PixelBuffer
from blitspersecond.resources import ImageSpec

from .sprite_engine import SpriteEngine

# RGB per plane name, the supercombo.gg reading: hurt = blues, attack = red,
# push = green. Unlisted names draw from _EXTRA in deterministic (sorted)
# order, so a package's custom vocabulary still colours stably run to run.
_PLANE_COLOURS: Dict[str, Tuple[int, int, int]] = {
    "attack": (255, 48, 48),
    "body_hurt": (48, 96, 255),
    "limb_hurt": (96, 168, 255),
    "push": (48, 216, 96),
    "throw": (255, 64, 255),
    "throwable": (64, 208, 224),
}
_EXTRA = (
    (255, 160, 48),
    (216, 216, 48),
    (168, 48, 255),
    (48, 255, 208),
)
_SILHOUETTE = (255, 255, 255)
_FILL_ALPHA = 72
_EDGE_ALPHA = 255
_SILHOUETTE_ALPHA = 36
_SILHOUETTE_INDEX = 1
_ANCHOR_INDEX = 2


class PlaneOverlay:
    """Debug: the live collision geometry painted onto an indexed canvas --
    supercombo.gg's hitbox view for any watched sprite layers.

        overlay = PlaneOverlay(p1_engine, p2_engine)
        boxes = TileEngine(overlay.buffer,               # above the sprites
                           tilewidth=overlay.buffer.width,
                           tileheight=overlay.buffer.height)
        boxes.blit(0, 0, 0)
        ...
        overlay.update()        # each tick (skip it, or disable the Layer,
                                # to hide the boxes)

    The one property that makes this trustworthy: it paints FROM the retained
    packed masks -- the exact bits every query tests -- not from a parallel
    transform. Facing, part offsets, collide=False ghosts and blank planes
    have already been resolved by collision composition.

    This remains a debug tool, but its canvas is one byte per pixel and uses
    a ConsolePalette for translucent fills. Named planes render with solid
    1px edges, the default silhouette as a faint tint, and each rich
    instance's anchor as a small cross.
    """

    def __init__(
        self,
        *engines: SpriteEngine,
        size: Tuple[int, int] = (640, 360),
        colours: Optional[Dict[str, Tuple[int, int, int]]] = None,
        silhouette: bool = True,
    ) -> None:
        self._engines = list(engines)
        self._silhouette = silhouette
        self._colours = dict(_PLANE_COLOURS)
        if colours:
            self._colours.update(colours)
        self._palette = ConsolePalette()
        self._palette[_SILHOUETTE_INDEX] = (
            *_SILHOUETTE,
            _SILHOUETTE_ALPHA,
        )
        self._palette[_ANCHOR_INDEX] = (*_SILHOUETTE, 255)
        self._plane_indices: Dict[str, Tuple[int, int]] = {}
        for engine in engines:
            for index, name in enumerate(sorted(engine.sheet.planes)):
                self._ensure_plane(name, self._colour(name, index))
        self._buffer = PixelBuffer(
            ImageSpec(
                size,
                "P",
                palette=self._palette,
                transparency_index=0,
            )
        )

    @property
    def buffer(self) -> PixelBuffer:
        """The canvas: hand it to a Layer (auto-wrapped by the picture
        engine) above the sprite layers it watches."""
        return self._buffer

    def watch(self, engine: SpriteEngine) -> None:
        self._engines.append(engine)
        for index, name in enumerate(sorted(engine.sheet.planes)):
            self._ensure_plane(name, self._colour(name, index))

    def _colour(self, name: str, index: int) -> Tuple[int, int, int]:
        return self._colours.get(name) or _EXTRA[index % len(_EXTRA)]

    def _ensure_plane(self, name: str, colour) -> Tuple[int, int]:
        indices = self._plane_indices.get(name)
        if indices is not None:
            return indices
        fill = 3 + len(self._plane_indices) * 2
        edge = fill + 1
        if edge >= 256:
            raise ValueError("PlaneOverlay supports at most 126 named planes")
        self._palette[fill] = (*colour, _FILL_ALPHA)
        self._palette[edge] = (*colour, _EDGE_ALPHA)
        indices = (fill, edge)
        self._plane_indices[name] = indices
        return indices

    def update(self) -> None:
        """Repaint the canvas from the watched engines' current state."""
        with self._buffer.edit() as px:
            px[:] = 0
            height, width = px.shape[0], px.shape[1]
            if self._silhouette:
                for engine in self._engines:
                    bits = engine.collision_mask_unpacked()
                    window = bits[:height, :width]
                    px[: window.shape[0], : window.shape[1]][
                        window
                    ] = _SILHOUETTE_INDEX
            for engine in self._engines:
                for i, name in enumerate(sorted(engine.sheet.planes)):
                    colour = self._colour(name, i)
                    bits = engine.collision_mask_unpacked((name,))
                    fill, edge = self._ensure_plane(name, colour)
                    self._paint_plane(
                        px, bits[:height, :width], fill, edge
                    )
            for engine in self._engines:
                for sprite in engine.sprites:
                    if sprite.visible:
                        x, y = sprite.position
                        self._paint_anchor(px, x, y, width, height)

    def _paint_plane(self, px, bits, fill, edge) -> None:
        """Tint a named packed plane and derive its visible one-pixel edge."""
        if not bits.any():
            return
        view = px[: bits.shape[0], : bits.shape[1]]
        view[bits] = fill
        interior = bits.copy()
        interior[0, :] = False
        interior[-1, :] = False
        interior[:, 0] = False
        interior[:, -1] = False
        interior[1:-1, 1:-1] &= (
            bits[:-2, 1:-1]
            & bits[2:, 1:-1]
            & bits[1:-1, :-2]
            & bits[1:-1, 2:]
        )
        view[bits & ~interior] = edge

    def _paint_anchor(self, px, x, y, width, height) -> None:
        arm = 3
        if 0 <= y < height:
            px[y, max(x - arm, 0) : min(x + arm + 1, width)] = _ANCHOR_INDEX
        if 0 <= x < width:
            px[max(y - arm, 0) : min(y + arm + 1, height), x] = _ANCHOR_INDEX
