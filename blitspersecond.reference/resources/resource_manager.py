from hashlib import sha256
from os.path import abspath
from typing import Any, Dict, TYPE_CHECKING


from blitspersecond.common import SingletonMeta

from .internal import Resource

if TYPE_CHECKING:
    from blitspersecond.graphics.common import PixelBuffer

    from .image import ImageSpec
    from .sound import SoundSpec
    from .sprite_sheet import SpriteSheetSpec
    from .tmx import TiledFile


class ResourceManager(metaclass=SingletonMeta):
    """creates a usable game object according to the spec the user passed in, delegates to resourceloader. Caches constructed object and returns Resource"""

    def __init__(self):
        # Values are heterogeneous by construction (one spec type per
        # accessor), so the dict is Any and each accessor's DECLARED
        # return type is what the caller sees. That declaration is the
        # entire reason these are four methods and not one load().
        self._cache: Dict[Any, Any] = {}

    def _filename_to_id(self, filename: str) -> int:
        abs_path = abspath(filename)
        hash_obj = sha256(abs_path.encode("utf-8"))
        return int.from_bytes(hash_obj.digest()[:8], byteorder="big")

    def image(self, filename: str) -> "ImageSpec":
        """Memo-cached ImageSpec for a file. Disk hit + PIL load + validation
        happen once (on miss); the cached spec is shared read-only -- PixelBuffer
        copies its data out, so the spec stays pristine."""
        f_id = self._filename_to_id(filename)
        if f_id not in self._cache:
            from .image import load_image_spec

            self._cache[f_id] = load_image_spec(filename)
        return self._cache[f_id]

    def sound(self, filename: str) -> "SoundSpec":
        """Memo-cached SoundSpec for a WAV file. Disk hit + decode happen once
        (on miss); the cached spec is shared read-only -- PCM reads it in
        place and never writes, so one decode serves every player of the
        sample."""
        f_id = self._filename_to_id(filename)
        if f_id not in self._cache:
            from .sound import load_sound_spec

            self._cache[f_id] = load_sound_spec(filename)
        return self._cache[f_id]

    def sprite_sheet(self, filename: str) -> "SpriteSheetSpec":
        """Memo-cached SpriteSheetSpec for a `.sprite.json` package. Parse +
        validation happen once (on miss); the tileset images ride the same
        cache via .image(), so the loader's bounds check and the eventual
        PixelBuffer construction share one disk hit."""
        f_id = self._filename_to_id(filename)
        if f_id not in self._cache:
            from .sprite_sheet import load_sprite_sheet_spec

            self._cache[f_id] = load_sprite_sheet_spec(filename)
        return self._cache[f_id]

    def tmx(self, filename: str) -> "TiledFile":
        """Memo-cached TiledFile for a BPS-compatible TMX file.

        Parsing, TSX validation and referenced image decoding happen once.
        Tiled is an external authoring tool: what comes back is a TiledFile,
        the BPS reading of it, which TileMap then turns into its own
        PixelBuffers/TileAtlases.
        """
        f_id = self._filename_to_id(filename)
        if f_id not in self._cache:
            from .tmx import load_tiled_file

            self._cache[f_id] = load_tiled_file(filename)
        return self._cache[f_id]

    def buffer(self, filename: str) -> "PixelBuffer":
        """Memo-cached PixelBuffer for an image file -- the SHARED sheet.

        `image()` caches the pristine spec; this caches the buffer built from
        it, and that is a different promise. Identity IS sharing for a
        PixelBuffer: every layer handed this one draws from a single texture
        and a single interned tile pool. Two TMX maps over one tileset, or a
        map and a hand-placed `layers.tile("same.png", ...)`, land on one
        sheet rather than silently duplicating it.

        Shared, therefore not yours to write into. Nothing in the engine
        mutates a loaded sheet -- recolouring goes through the palette and
        out to the shader, never through the pixels -- so this is safe by
        construction. A caller that genuinely needs destructive edits (eating
        the dirt in a Boulder Dash) takes `TileMap.copy()`, which mints a
        private buffer with its own texture.
        """
        f_id = self._filename_to_id(filename)
        key = ("buffer", f_id)
        if key not in self._cache:
            from blitspersecond.graphics.common import PixelBuffer

            self._cache[key] = PixelBuffer(self.image(filename))
        return self._cache[key]

    def fetch(self, filename: str) -> Resource:
        """Memo-cached text file (shader source is the one real client).
        Content lazy-reads on first .content access."""
        f_id = self._filename_to_id(filename)
        if f_id not in self._cache:
            self._cache[f_id] = Resource(filename, f_id)
        return self._cache[f_id]

    def remove(self, filename: str):
        """Evict a cached entry by filename (specs carry no cache id)."""
        f_id = self._filename_to_id(filename)
        if f_id in self._cache:
            entry = self._cache[f_id]
            del self._cache[f_id]
            return entry
        raise KeyError("Resource not found in cache.")

    def reset(self, are_you_sure: bool = False):
        if are_you_sure:
            self._cache = {}

    def __len__(self):
        return len(self._cache)
