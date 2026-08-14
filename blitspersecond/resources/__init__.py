"""The loading dock: files become specs here, once, and everything shares them.

    from blitspersecond.resources import ResourceManager, load_image_spec

`ResourceManager` is the memo-cache singleton: `.image(path)` -> `ImageSpec`,
`.sound(path)` -> `SoundSpec`, `.sprite_sheet(path)` -> `SpriteSheetSpec`,
each loaded and validated exactly once and shared read-only from then on
(PixelBuffer copies image data out; the audio `PCM` operator reads sound data in
place). This is the bottom of the dependency stack -- display -> graphics ->
resources -- so everything above consumes these types and nothing here knows
they exist.

The specs are pure value objects, no GL, no device state: `ImageSpec` (size,
mode P/RGB/RGBA, pixels; indexed transparency must sit at palette index 0),
`SoundSpec` (decoded samples at native rate) and `SpriteSheetSpec` (a whole
sprite package -- tilesets, assemblies, animations -- loaded from the
interchange JSON with every format invariant checked; see sprite_sheet.py).
`Palette` now lives in `blitspersecond.colors` (colour vocabulary, not loading)
and is re-exported here for the specs that carry it. `load_image_spec(path)`
/ `load_sprite_sheet_spec(path)` are the direct loaders when you want a spec
without cache identity.

The appendix, `resources.internal`, holds `Resource` -- the cached text file
behind `ResourceManager.fetch()`, which exists so the graphics Shader can pull
.vert/.frag source; user code never meets it.
"""

from .resource_manager import ResourceManager
# Palette moved down into blitspersecond.colors (colour vocabulary, not loading);
# re-exported here so `from blitspersecond.resources import Palette` still works.
from blitspersecond.colors import ConsolePalette, Palette
from .image import ImageSpec, load_image_spec
from .sound import SoundSpec
from .sprite_sheet import SpriteSheetSpec, load_sprite_sheet_spec

__all__ = [
    "ResourceManager",
    "Palette",
    "ConsolePalette",
    "ImageSpec",
    "load_image_spec",
    "SoundSpec",
    "SpriteSheetSpec",
    "load_sprite_sheet_spec",
]
