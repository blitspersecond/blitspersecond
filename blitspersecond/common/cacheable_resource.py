"""The loading pattern, once, so no two resource types can drift apart.

Lives in `common` because the things that load from disk do not live
together: `PixelBuffer` is in `graphics`, sound will be in the audio engine,
and a tile map is a graphics concern that happens to read a file. There is no
"resources" layer for this to sit in -- a resource type belongs with what
consumes it, and what they share is this behaviour, not an address.

**This replaced `ResourceManager`**, a singleton holding one dict for every
resource type, with a method per type that imported its loader inside the
method body to dodge an import cycle. It was one object that had to be edited
every time a new kind of file could be loaded, and its cache was keyed on a
truncated sha256 of the path -- a hash of a string, to use as a dict key,
which a dict was going to hash anyway.
"""

from os.path import abspath
from typing import Any, Hashable, MutableMapping, Optional
from weakref import WeakValueDictionary


class CacheableResource:
    """Base for a resource that exists once per file, for as long as it is used.

    Naming the same file twice gives you the SAME OBJECT:

        PixelBuffer("island.png") is PixelBuffer("island.png")    # True

    **This is interning, and it is deliberate.** It is right here for one
    reason: these types own something scarce. A `PixelBuffer` is a texture on
    the graphics card, and two of them over one file is two uploads, twice the
    VRAM, and -- once tiles exist -- two separate interned tile pools that can
    never share a draw call. Identity IS the meaning of the type; the id names
    the texture. Handing back a second one would not be a neutral choice, it
    would be the specific waste the type exists to prevent, and it would be
    invisible when it happened.

    **A caller who wants an independent one says `copy()`**, which mints a new
    identity, a new texture and its own pixels. That escape is what makes
    interning safe, and a resource type without one has no business
    inheriting this.

    Note the shape of that argument, because it is exactly inverted from a
    plain value object. A type holding nothing scarce should NOT intern: there
    is no waste to prevent, sharing is already spelled `a = b`, and a
    constructor that sometimes declines to construct is a surprise bought for
    nothing. Scarcity plus `copy()` is what flips it.

    ## Weakly held, so this is a cache and not a leak

    The table holds weak references. A resource lives while something is using
    it -- a layer, a map, a sheet -- and when the last holder lets go it is
    collected, its texture is released, and anything anchored to its lifecycle
    (tile pools reclaim this way) gets to run. Name the file again afterwards
    and it loads fresh.

    A strong table was tried in the old `ResourceManager` and it quietly
    disabled the reclaim: tiles were freed by watching a buffer die, and the
    cache guaranteed no buffer ever did. Switch level twice and both levels'
    art stayed resident, with a `remove()` method as the only escape and
    nothing calling it. Two mechanisms that contradicted each other now agree.

    The cost is honest and visible: a caller who builds a resource, drops it,
    and builds it again re-reads the file. That shows up as stutter, which is
    a symptom you can find, rather than as memory that never comes back.

    ## Writing one

    A subclass fills in two hooks:

      `_cache_key(...)`  a classmethod over the constructor's arguments,
                         returning the key to intern under -- or None for
                         "this one has no identity, build it fresh". Loading
                         a path returns `cls._path_key(path)`; being handed
                         raw pixels returns None, because an array you built
                         is not the same thing twice.
      `_load(...)`       builds the instance. Runs once per interned key,
                         taking the same arguments the constructor did.

    **A SUBCLASS MUST NOT DEFINE `__init__`.** Enforced below rather than
    documented and hoped for, because the failure is silent: Python calls
    `__init__` on whatever `__new__` returns, INCLUDING a cache hit, so a
    subclass that loads in `__init__` re-reads the file on every construction
    while the cache sits there holding a correct answer. Nothing breaks and
    nothing warns. `_load` is where that code goes.
    """

    _instances: MutableMapping[Hashable, Any]

    def __init_subclass__(cls, **kwargs) -> None:
        super().__init_subclass__(**kwargs)
        # Each subclass gets its OWN table. Inheriting one from the base
        # would have every PixelBuffer and every Sample in one keyspace.
        cls._instances = WeakValueDictionary()
        if "__init__" in cls.__dict__:
            raise TypeError(
                f"{cls.__name__} defines __init__, which a CacheableResource "
                f"must not: __init__ runs again on every cache HIT, so the "
                f"work in it would repeat for a resource that is already "
                f"loaded. Move it to _load(), which runs once per key."
            )

    @classmethod
    def _path_key(cls, filename: str) -> str:
        """The interning key for a file: its absolute path.

        The path itself, not a hash of it -- a dict hashes the string anyway,
        so digesting it first bought nothing and cost the ability to read a
        cache entry, evict by name, or say which file a key meant.
        """
        return abspath(filename)

    @classmethod
    def _cache_key(cls, *args, **kwargs) -> Optional[Hashable]:
        """What to intern this construction under; None to skip the cache."""
        raise NotImplementedError

    def _load(self, *args, **kwargs) -> None:
        """Build this instance. Runs once per interned key."""
        raise NotImplementedError

    def __new__(cls, *args, **kwargs):
        key = cls._cache_key(*args, **kwargs)
        if key is not None:
            cached = cls._instances.get(key)
            if cached is not None:
                return cached
        resource = super().__new__(cls)
        resource._load(*args, **kwargs)
        # Registered AFTER the load, so a load that raises leaves nothing
        # behind: a half-built resource in the table would be handed out,
        # intact-looking, to every later caller.
        if key is not None:
            cls._instances[key] = resource
        return resource

    # -- cache administration ------------------------------------------------

    @classmethod
    def reload(cls, filename: str) -> bool:
        """Drop the shared entry for a file so the next construction re-reads
        it. True if one was held.

        For art that changed on disk. Objects already handed out keep working
        and keep their contents -- they are ordinary references, not cache
        lookups -- so this changes what the NEXT caller gets and never what is
        already on screen. Which also means the old and new versions coexist
        until the last holder of the old one lets go.
        """
        key = cls._path_key(filename)
        if key not in cls._instances:
            return False
        del cls._instances[key]
        return True

    @classmethod
    def live(cls) -> int:
        """How many of this resource type are currently loaded and in use.

        Weakly held, so this falls on its own as holders let go; a number that
        only ever climbs means something is retaining every resource it has
        ever touched.
        """
        return len(cls._instances)
