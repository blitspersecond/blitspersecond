from hashlib import sha256
from os.path import abspath


from blitspersecond.common import SingletonMeta

from .internal import Resource


class ResourceManager(metaclass=SingletonMeta):
    """creates a usable game object according to the spec the user passed in, delegates to resourceloader. Caches constructed object and returns Resource"""

    def __init__(self):
        self._cache = {}

    def _filename_to_id(self, filename: str) -> int:
        abs_path = abspath(filename)
        hash_obj = sha256(abs_path.encode("utf-8"))
        return int.from_bytes(hash_obj.digest()[:8], byteorder="big")

    def image(self, filename: str):
        """Memo-cached ImageSpec for a file. Disk hit + PIL load + validation
        happen once (on miss); the cached spec is shared read-only -- PixelBuffer
        copies its data out, so the spec stays pristine."""
        f_id = self._filename_to_id(filename)
        if f_id not in self._cache:
            from .image import load_image_spec

            self._cache[f_id] = load_image_spec(filename)
        return self._cache[f_id]

    def sound(self, filename: str):
        """Memo-cached SoundSpec for a WAV file. Disk hit + decode happen once
        (on miss); the cached spec is shared read-only -- PCM reads it in
        place and never writes, so one decode serves every player of the
        sample."""
        f_id = self._filename_to_id(filename)
        if f_id not in self._cache:
            from .sound import load_sound_spec

            self._cache[f_id] = load_sound_spec(filename)
        return self._cache[f_id]

    def sprite_sheet(self, filename: str):
        """Memo-cached SpriteSheetSpec for a `.sprite.json` package. Parse +
        validation happen once (on miss); the tileset images ride the same
        cache via .image(), so the loader's bounds check and the eventual
        PixelBuffer construction share one disk hit."""
        f_id = self._filename_to_id(filename)
        if f_id not in self._cache:
            from .sprite_sheet import load_sprite_sheet_spec

            self._cache[f_id] = load_sprite_sheet_spec(filename)
        return self._cache[f_id]

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
