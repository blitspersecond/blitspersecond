from math import ceil, floor
from typing import Optional, Tuple, Union

import numpy as np
from pyglet.gl import GL_TEXTURE0, GL_TEXTURE1, glActiveTexture, glBindTexture
from pyglet.image import Texture

from blitspersecond.common import Size
from blitspersecond.display.layer import Layer
from blitspersecond.graphics.common import Collidable, CollisionQuery, PixelBuffer
from blitspersecond.graphics.internal import PaletteTexture, Shader
from blitspersecond.colors import TRANSPARENT, system_palette
from blitspersecond.resources import ImageSpec, Palette, ResourceManager

from .tile_atlas import TileAtlas, TileIndex
from .tile import Tile
from .batch import TileBatch, TileFlags
from .collision import CollisionMask

# The bounded local tilemap: one structured row per live stamp. A coarse
# camera step translates these origins in one NumPy stroke, culls rows outside
# the one-tile-bordered plane, and compacts survivors through permanent scratch.
_TILE_DTYPE = np.dtype(
    [
        ("target", np.int32, 2),
        ("source", np.uint16, 2),
        ("size", np.uint16, 2),
        ("flags", np.uint8),
    ]
)

# The shared 1x1 white RGBA buffer behind every layer background fill: one
# texture process-wide, coloured per draw through the batch colour register.
_WHITE_BUFFER: Optional[PixelBuffer] = None


def _white() -> PixelBuffer:
    global _WHITE_BUFFER
    if _WHITE_BUFFER is None:
        _WHITE_BUFFER = PixelBuffer(
            ImageSpec(
                size=(1, 1),
                mode="RGBA",
                data=np.full((1, 1, 4), 255, dtype=np.uint8),
            )
        )
    return _WHITE_BUFFER


class TileLayer(Layer):
    """A tile layer: retained tile placements over one TileAtlas, drawn as a
    single TileBatch -- and a Layer, so the thing you blit on is the thing
    you place in the draw order and ask collision questions of:

        background.before(foreground)
        background.collides_with(foreground).at(x, y, w, h)

    Produced by the TileEngine factory (the mainline); constructing TileLayer
    directly builds the same object *unattached* -- not in any draw order.

    Tiles uniformly match the atlas grid size, which may be rectangular just
    as it is in Tiled. Transpose and quarter-turn flags swap the placement's
    displayed width/height; flips preserve them.

    tilewidth/tileheight are Tiled's vocabulary for pixels per tile, and are
    required unless you pass a ready-made TileAtlas. A PixelBuffer is accepted
    too, for when the pixels came from somewhere other than a file.

    HAS-A TileAtlas: the atlas answers "what is tile i"; TileEngine is a finite
    local tilemap around the 640x360 viewport. Games stamp the visible cells
    plus a one-tile border. Fine camera movement is one GPU translate; crossing
    an atlas-cell boundary broadcasts the coarse translation into local
    origins, destructively culls outgoing stamps, compacts the VBO, and leaves
    the exposed border empty for a map streamer to refill.

    One atlas per TileEngine, exactly: every placement samples the atlas's
    single texture, which is what buys the draw ZERO mid-batch texture binds
    -- the GL context switch batching exists to avoid. blit() enforces it: a
    Tile from any other buffer is rejected. A second sheet is
    a second TileEngine on a second Layer, never a second texture in this one.

    Indexed (mode "P") surfaces are the native path: palette machinery and
    collision (which reads raw palette indices as per-pixel solidity). Direct
    RGB/RGBA surfaces draw through the same quad batch with the direct shader
    instead -- full colour, but display-only: no palette, no collision mask.
    The layer background colour (`color`) works in both modes. Independently
    moving bullets and particles are sprites, not tilemap stamps.

    blit() needs a current GL context (shader fetch + texture-region UVs) --
    a given, since layers are built after the engine (and its window) exist.
    """

    def __init__(
        self,
        source: Union[str, PixelBuffer, TileAtlas],
        tilewidth: Optional[int] = None,
        tileheight: Optional[int] = None,
        collidable: bool = False,
    ) -> None:
        atlas = self._as_atlas(source, tilewidth, tileheight)
        self._buffer = atlas.buffer
        self._atlas = atlas
        # The shared per-mode program, fetched lazily on first blit/draw. A
        # shared program has one uniform state, so per-instance uniforms
        # (the indexed palette) are set per draw, not once at build.
        self._shader: Optional[Shader] = None
        # The layer background colour: what the whole layer surface shows
        # behind the tiles. Held as the value the caller set (a named system
        # index or an RGB(A) tuple) plus its resolved draw-time floats;
        # painted by a one-quad batch built lazily on first need.
        self._color: Union[int, Tuple[int, ...]] = TRANSPARENT
        self._bg_rgba: Tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
        self._bg_batch: Optional[TileBatch] = None
        # The colour registers (inherited from the retired PictureEngine,
        # same names): whole-layer
        # rgba multipliers, default 1.0 = the art as authored. Fed to the
        # batch as one constant attribute per draw -- fading or tinting a
        # whole layer writes no buffers. Works in both modes: both frag
        # shaders multiply by vertex_colors.
        self._tint = [1.0, 1.0, 1.0, 1.0]
        self._collidable: bool = False
        # Collision is an indexed-surface capability: solidity IS the palette
        # index. A direct (RGB/RGBA) layer has no index to read, so it gets
        # no mask at all -- display-only, enforced by the collidable setter.
        self._collision = CollisionMask(atlas) if atlas.buffer.indexed else None
        self.collidable = collidable
        # Capacity-doubled SoA plus permanent coarse-cull scratch.
        self._tiles: np.ndarray = np.zeros(0, dtype=_TILE_DTYPE)
        self._tiles_scratch: np.ndarray = np.zeros(0, dtype=_TILE_DTYPE)
        self._ntiles: int = 0
        # GPU side (created lazily at first blit -- needs GL) plus the
        # (cap, 4, 3) float32 staging array the corner expansion writes into
        # before the one-shot upload.
        self._tilebatch: Optional[TileBatch] = None
        self._verts: np.ndarray = np.zeros((0, 4, 3), dtype=np.float32)
        # Public absolute camera, the cell already baked into local origins,
        # and the sub-cell remainder actually sent to the GPU.
        self._scroll: Tuple[float, float] = (0.0, 0.0)
        self._coarse: Tuple[int, int] = (0, 0)
        self._fine_scroll: Tuple[float, float] = (0.0, 0.0)
        # Upload only when stamps change or a coarse boundary compacts them.
        self._dirty_sync: bool = True
        # IS-A Layer: this object is its own content; the compositor's
        # _composite() lands on the override below. Enabled from birth.
        super().__init__()

    @staticmethod
    def _as_atlas(
        source: Union[str, PixelBuffer, TileAtlas],
        tilewidth: Optional[int],
        tileheight: Optional[int],
    ) -> TileAtlas:
        """Accept the thing the caller actually has: a path, a buffer, or a
        ready-made atlas. Loading and grid-wrapping are ceremony the engine
        can perform itself -- nothing outside this package consumes a
        TileAtlas, so building one was never the caller's errand.

        tilewidth/tileheight are Tiled's own vocabulary and mean pixels per
        tile (Tiled's map-level width/height count cells; these do not). They
        are REQUIRED for a path or a buffer: the atlas's own default is the
        whole image as a single tile, which is silently wrong for every real
        tileset, so omitting them here is an error rather than one giant tile.
        """
        if isinstance(source, TileAtlas):
            if tilewidth is not None or tileheight is not None:
                raise TypeError(
                    "tilewidth/tileheight do not apply to a ready-made "
                    "TileAtlas -- it already carries its grid"
                )
            return source

        if isinstance(source, str):
            source = PixelBuffer(ResourceManager().image(source))
        if not isinstance(source, PixelBuffer):
            raise TypeError(
                f"expected an image path, PixelBuffer or TileAtlas, got {type(source)}"
            )
        if tilewidth is None or tileheight is None:
            raise TypeError(
                "tilewidth and tileheight are required (pixels per tile) -- "
                "pass a ready-made TileAtlas if you want its grid instead"
            )
        return TileAtlas(source, (int(tilewidth), int(tileheight)))

    @property
    def atlas(self) -> TileAtlas:
        return self._atlas

    @property
    def buffer(self):
        return self._buffer

    def _ensure_built(self) -> None:
        """Fetch this engine's shared program on first use (compiles on the
        very first fetch engine-wide -- needs a current GL context)."""
        if self._shader is None:
            frag = "indexed" if self._buffer.indexed else "direct"
            self._shader = Shader.get("default", frag)

    @property
    def tile_size(self) -> Size:
        """The atlas's (width, height) of one tile (read-through convenience)."""
        return self._atlas.tile_size

    @property
    def collidable(self) -> bool:
        """Whether this layer's placements participate in collision detection.

        On an indexed surface this is pure intent -- collision reads raw
        palette indices as per-pixel solidity, and mask upkeep costs, so only
        layers that answer collision questions should pay it. A direct
        (RGB/RGBA) layer has no indices to read: it is display-only, and
        setting True raises."""
        return self._collidable

    @collidable.setter
    def collidable(self, value: bool) -> None:
        value = bool(value)
        if value and self._collision is None:
            raise ValueError(
                "collision needs an indexed (palette) surface -- an RGB/RGBA "
                "tile layer is display-only"
            )
        self._collidable = value

    def _resolve(self, tile: Union[Tile, TileIndex]) -> Tile:
        """A Tile as-is (guarded), or an index resolved through the atlas.

        The one-atlas-per-TileEngine guard lives here: accepting a Tile from
        another buffer would force a mid-batch texture bind, which is the
        exact GL context switch the TileBatch exists to avoid."""
        if isinstance(tile, Tile):
            if tile.buffer is not self._buffer:
                raise ValueError(
                    f"{tile!r} samples a different atlas; one atlas per "
                    "TileEngine -- draw it from its own TileEngine/Layer instead"
                )
            expected = self._atlas.tile_size
            if (tile.size.x, tile.size.y) != expected:
                raise ValueError(
                    f"{tile!r} has size {(tile.size.x, tile.size.y)}; "
                    f"TileEngine placements must match its atlas tile_size "
                    f"{tuple(expected)}"
                )
            return tile
        return self._atlas.tile(tile)

    @staticmethod
    def _resolve_flags(flags: TileFlags) -> TileFlags:
        """Normalize one placement's Tiled orientation bits."""
        return TileFlags(int(flags) & 7)

    @staticmethod
    def _oriented_size(tile: Tile, flags: TileFlags) -> Tuple[int, int]:
        if flags & TileFlags.TRANSPOSE:
            return tile.size.y, tile.size.x
        return tile.size.x, tile.size.y

    def _drawable_bounds(self) -> Tuple[int, int, int, int]:
        """The tile-aligned viewport plus one atlas-cell border."""
        from blitspersecond.system.config import Config

        disp = Config().display
        tw, th = self.tile_size
        return (
            -tw,
            -th,
            ceil(disp.width / tw) * tw + tw,
            ceil(disp.height / th) * th + th,
        )

    # -- blit (retained placement) ---------------------------------------

    def blit(
        self,
        tile: Union[Tile, TileIndex],
        x: int,
        y: int,
        flags: TileFlags = TileFlags.NONE,
    ) -> None:
        """Stamp a source tile at local pixel origin (x, y).

        Appends a row to the DOD SoA (the source of truth) and writes the tile's
        texture rect into the TileBatch -- no per-placement Python object exists
        anywhere. Stamps must intersect the finite viewport + one-cell border;
        a complete TMX map remains game-side.
        """
        self._ensure_built()  # shader must exist (needs a GL context)
        assert self._shader is not None  # built above
        if self._tilebatch is None:
            self._tilebatch = TileBatch(self._shader.program, len(self._tiles))
        t = self._resolve(tile)
        flags = self._resolve_flags(flags)
        display_size = self._oriented_size(t, flags)
        left, top, right, bottom = self._drawable_bounds()
        dw, dh = display_size
        if x + dw <= left or y + dh <= top or x >= right or y >= bottom:
            raise ValueError(
                f"tile stamp at {(x, y)} with size {display_size} lies outside "
                f"the bounded drawable area {(left, top, right, bottom)}"
            )
        idx = self._ntiles
        self._ensure_tile_capacity(idx + 1)
        row = self._tiles[idx]
        row["target"] = (x, y)
        row["source"] = (t.source.x, t.source.y)
        row["size"] = display_size
        row["flags"] = int(flags)
        self._ntiles = idx + 1
        self._dirty_sync = True
        region = self._buffer.texture.get_region(
            t.source.x, t.source.y, t.size.x, t.size.y
        )
        self._tilebatch.set_quad(idx, region.tex_coords, flags)
        if self._collidable:
            # Border stamps live in CollisionMask's retained apron until fine
            # scrolling brings them into the queryable screen interior.
            self._collision.stamp(
                x - int(self._fine_scroll[0]),
                y - int(self._fine_scroll[1]),
                (t.source.x, t.source.y),
                (t.size.x, t.size.y),
                int(flags),
            )

    def _ensure_tile_capacity(self, n: int) -> None:
        """Grow the SoA (and its GPU projection) to hold >= n rows, doubling so
        blit() amortises O(1)."""
        cap = len(self._tiles)
        if n <= cap:
            return
        newcap = max(8, cap * 2, n)
        grown = np.zeros(newcap, dtype=_TILE_DTYPE)
        grown[:cap] = self._tiles
        self._tiles = grown
        self._tiles_scratch = np.zeros(newcap, dtype=_TILE_DTYPE)
        verts = np.zeros((newcap, 4, 3), dtype=np.float32)
        verts[: len(self._verts)] = self._verts
        self._verts = verts
        if self._tilebatch is not None:
            self._tilebatch.resize(newcap)

    def _translate_and_cull(self, dx: int, dy: int) -> int:
        """Bake one coarse camera step and compact surviving local stamps."""
        n = self._ntiles
        if n == 0:
            return 0
        tiles = self._tiles[:n]
        tiles["target"][:, 0] -= dx
        tiles["target"][:, 1] -= dy
        x, y = tiles["target"][:, 0], tiles["target"][:, 1]
        w, h = tiles["size"][:, 0], tiles["size"][:, 1]
        left, top, right, bottom = self._drawable_bounds()
        keep = (x + w > left) & (y + h > top) & (x < right) & (y < bottom)
        indices = np.flatnonzero(keep)
        kept = len(indices)
        if kept != n:
            if kept:
                np.take(
                    self._tiles,
                    indices,
                    axis=0,
                    out=self._tiles_scratch[:kept],
                )
            self._tiles, self._tiles_scratch = (
                self._tiles_scratch,
                self._tiles,
            )
            assert self._tilebatch is not None
            self._tilebatch.compact(indices)
            self._ntiles = kept
        self._dirty_sync = True
        return n - kept

    # -- bounded camera ----------------------------------------------------

    @property
    def scroll(self) -> Tuple[float, float]:
        """The caller's absolute camera position.

        Only its sub-cell remainder reaches the GPU. Whole-cell changes are
        baked destructively into this engine's bounded local tilemap."""
        return self._scroll

    @scroll.setter
    def scroll(self, value: Tuple[float, float]) -> None:
        sx, sy = float(value[0]), float(value[1])
        if (sx, sy) != self._scroll:
            ox, oy = self._scroll
            tw, th = self.tile_size
            ncx, ncy = floor(sx / tw), floor(sy / th)
            ocx, ocy = self._coarse
            dx, dy = (ncx - ocx) * tw, (ncy - ocy) * th
            self._scroll = (sx, sy)
            self._coarse = (ncx, ncy)
            self._fine_scroll = (sx - ncx * tw, sy - ncy * th)
            if self._collidable:
                self._collision.translate(int(ox), int(oy), int(sx), int(sy))
            if dx or dy:
                self._translate_and_cull(dx, dy)

    # -- collision (delegated to the composed mask) ------------------------

    def restamp(self) -> None:
        """Explicitly rebuild collision from the current local tilemap."""
        if self._collision is None:
            return
        if not self._collidable or self._ntiles == 0:
            self._collision.clear()
            return
        self._collision.restamp(
            self._tiles[: self._ntiles], self._fine_scroll
        )

    def clear_collision_mask(self) -> None:
        """Zero this tile engine's persistent packed collision mask (apron
        included).

        Primarily a diagnostic/rebuild primitive; ordinary scrolling and
        incoming stamps maintain the bordered mask incrementally."""
        if self._collision is not None:
            self._collision.clear()

    @property
    def collision_mask(self) -> np.ndarray:
        """The packed screen-space collision mask (see CollisionMask.mask).
        All zero when .collidable is False or nothing is stamped."""
        if self._collision is None:
            raise ValueError("an RGB/RGBA tile layer keeps no collision mask")
        return self._collision.mask

    @property
    def collision_mask_unpacked(self) -> np.ndarray:
        """Debug/viewer convenience (see CollisionMask.unpacked)."""
        if self._collision is None:
            raise ValueError("an RGB/RGBA tile layer keeps no collision mask")
        return self._collision.unpacked

    def collides_with(self, other: Collidable) -> CollisionQuery:
        """Prepare a pairwise pixel-perfect collision question against any
        other collidable engine (another TileEngine, a Text layer):
        `player.collides_with(ladders).at(x, y, w, h)`.

        Returns a CollisionQuery; nothing is computed until .at() runs. Both
        sides must be collidable -- a layer that was never meant to
        collide answering False forever is a silent bug, so this raises
        instead. Detail is expressed by asking more boxes (hitbox, hurtbox),
        not by richer answers: .at() returns bool, nothing else."""
        if not self._collidable:
            raise ValueError("collides_with() on a non-collidable tile engine")
        if not other.collidable:
            raise ValueError(f"collides_with() target {other!r} is not collidable")
        return CollisionQuery(self, other)

    # -- DOD render path: vectorised expansion + one-shot upload -----------

    def prepare(self) -> None:
        """Per-frame render prep: lazy build + dirty re-upload, then project
        the compact SoA onto the GPU -- vectorised corner expansion, ONE buffer
        upload. Fine camera movement never dirties it."""
        self._ensure_built()
        # Touching .texture re-uploads iff dirty; the texture id is stable, so
        # nothing downstream rebuilds -- this is the writable-layer path.
        _ = self._buffer.texture
        n = self._ntiles
        if n == 0 or not self._dirty_sync or self._tilebatch is None:
            return
        self._dirty_sync = False
        tiles = self._tiles[:n]
        tx, ty = tiles["target"][:, 0], tiles["target"][:, 1]
        w, h = tiles["size"][:, 0], tiles["size"][:, 1]
        # Corner expansion: local origin + oriented extent.
        x2, y2 = tx + w, ty + h
        v = self._verts[:n]
        v[:, 0, 0] = tx
        v[:, 0, 1] = ty
        v[:, 1, 0] = x2
        v[:, 1, 1] = ty
        v[:, 2, 0] = x2
        v[:, 2, 1] = y2
        v[:, 3, 0] = tx
        v[:, 3, 1] = y2
        self._tilebatch.upload_positions(v)

    def _composite(self) -> None:
        """Background fill first, then palette bind (indexed only), then
        the tiles at the fine camera offset."""
        self.prepare()
        assert self._shader is not None  # built by prepare()
        if self._bg_rgba[3] > 0.0:
            # The layer's colour: one full-surface quad drawn before the
            # state transition into the tile batch. It rides the colour
            # registers like the tiles do, so fading the layer out takes
            # the backdrop with it.
            if self._bg_batch is None:
                self._ensure_background()
            assert self._bg_batch is not None
            r, g, b, a = self._bg_rgba
            tr, tg, tb, ta = self._tint
            self._bg_batch.draw(
                _white().texture, 1, colors=(r * tr, g * tg, b * tb, a * ta)
            )
        if self._buffer.indexed:
            # The palette rides texture unit 1 (palette RAM as a 256x1
            # texture, see PaletteTexture) -- bound per draw, uploaded only on
            # mutation, so palette swaps stay free. Unit 0 is re-activated
            # for the TileBatch, which binds the atlas there.
            self._shader.program["palette_texture"] = 1
            ptex = PaletteTexture.get(self._buffer.palette)
            glActiveTexture(GL_TEXTURE1)
            glBindTexture(ptex.target, ptex.id)
            glActiveTexture(GL_TEXTURE0)
        if self._tilebatch is not None and self._ntiles:
            sx, sy = self._fine_scroll
            self._tilebatch.draw(
                self._buffer.texture,
                self._ntiles,
                translate=(-float(int(sx)), -float(int(sy))),
                colors=tuple(self._tint),
            )

    # -- layer-surface metadata, delegated to the buffer ---------------------

    @property
    def texture(self) -> Texture:
        return self._buffer.texture

    @property
    def size(self) -> Size:
        return self._buffer.size

    @property
    def indexed(self) -> bool:
        return self._buffer.indexed

    @property
    def transparency_index(self) -> Optional[int]:
        return self._buffer.transparency_index

    @property
    def palette(self) -> Optional[Palette]:
        return self._buffer.palette

    # -- the layer background colour -----------------------------------------

    @property
    def color(self) -> Union[int, Tuple[int, ...]]:
        """The layer's background colour -- what the whole surface shows
        behind the tiles. Defaults to TRANSPARENT (no fill; layers beneath
        show through, tiles or none).

            background.color = CYAN            # a named system colour
            background.color = (36, 146, 219)  # or RGB / RGBA bytes

        Drawn as one full-layer quad before the tiles, so it costs one
        trivial draw and works for indexed and direct layers alike. Where
        indexed art keys out a colour that is really part of the scene
        (Sonic's sky IS its bg tileset's transparent index), this is how the
        scene gets it back. Reads back whatever form was assigned.
        """
        return self._color

    @color.setter
    def color(self, value: Union[int, Tuple[int, ...]]) -> None:
        if isinstance(value, (tuple, list)):
            channels = [int(c) for c in value]
            if len(channels) not in (3, 4) or not all(
                0 <= c <= 255 for c in channels
            ):
                raise ValueError(
                    f"colour tuple must be 3 or 4 bytes (0..255), got {value!r}"
                )
            r, g, b = channels[:3]
            a = channels[3] if len(channels) == 4 else 255
            self._color = tuple(channels)
            self._bg_rgba = (r / 255.0, g / 255.0, b / 255.0, a / 255.0)
            return
        index = int(value)
        if not 0 <= index <= 255:
            raise ValueError(
                f"colour must be a system palette index 0..255 or an RGB(A) "
                f"tuple, got {value!r}"
            )
        self._color = index
        if index == TRANSPARENT:
            self._bg_rgba = (0.0, 0.0, 0.0, 0.0)
            return
        r, g, b, _ = system_palette()[index]
        self._bg_rgba = (r / 255.0, g / 255.0, b / 255.0, 1.0)

    def _ensure_background(self) -> None:
        """The one-quad batch behind the tiles: a shared 1x1 white texture
        stretched over the layer surface, coloured per draw through the same
        constant-attribute register the tiles use. Built once, on the first
        composite that actually has a colour."""
        from blitspersecond.system.config import Config

        shader = Shader.get("default", "direct")
        batch = TileBatch(shader.program, 1)
        disp = Config().display
        w, h = float(disp.width), float(disp.height)
        batch.set_quad(0, _white().texture.tex_coords, TileFlags.NONE)
        batch.upload_positions(
            np.array(
                [[[0, 0, 0], [w, 0, 0], [w, h, 0], [0, h, 0]]],
                dtype=np.float32,
            )
        )
        self._bg_batch = batch


    # -- colour registers (0.0..1.0 multipliers, default 1.0) ---------------
    # The layer colour registers: rgb toward 0 fades to black,
    # alpha toward 0 fades out, uneven rgb tints. One constant attribute per
    # draw, applied to tiles and background fill alike, so a layer fades as
    # one thing.

    @property
    def red(self) -> float:
        return self._tint[0]

    @red.setter
    def red(self, value: float) -> None:
        self._tint[0] = min(max(float(value), 0.0), 1.0)

    @property
    def green(self) -> float:
        return self._tint[1]

    @green.setter
    def green(self, value: float) -> None:
        self._tint[1] = min(max(float(value), 0.0), 1.0)

    @property
    def blue(self) -> float:
        return self._tint[2]

    @blue.setter
    def blue(self, value: float) -> None:
        self._tint[2] = min(max(float(value), 0.0), 1.0)

    @property
    def alpha(self) -> float:
        return self._tint[3]

    @alpha.setter
    def alpha(self, value: float) -> None:
        self._tint[3] = min(max(float(value), 0.0), 1.0)


class TileEngine:
    """The static factory: make a TileLayer and put it on screen.

        background = TileEngine("tiles/background.png",
                                tilewidth=8, tileheight=8)
        background.blit(1, x, y)
        background.before(foreground)

    Calling it returns a TileLayer (__new__ hands back the layer; there is
    never a TileEngine instance), appended at the front of the running
    engine's draw order -- making one IS showing it. Placing it and toggling
    layer.enabled from there is the caller's business.

    The factory carries the making; TileLayer carries the data. If no
    BlitsPerSecond engine exists yet (GL-free tests, offline tools), the
    layer is returned unattached, exactly as TileLayer(...) would build it.
    """

    def __new__(
        cls,
        source: Union[str, PixelBuffer, TileAtlas],
        tilewidth: Optional[int] = None,
        tileheight: Optional[int] = None,
        collidable: bool = False,
    ) -> "TileLayer":
        layer = TileLayer(source, tilewidth, tileheight, collidable)
        # Peek, don't boot: append to the running engine's compositor if
        # there is one. SingletonMeta._instances is the singleton's registry;
        # constructing BlitsPerSecond here would drag a whole engine (window,
        # audio, input) into any context that merely built a tile layer.
        from blitspersecond.blitspersecond import BlitsPerSecond
        from blitspersecond.common.singleton_meta import SingletonMeta

        bps = SingletonMeta._instances.get(BlitsPerSecond)
        if bps is not None:
            bps.layers.add(layer)
        return layer
