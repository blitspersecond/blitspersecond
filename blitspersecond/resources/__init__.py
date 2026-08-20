"""The loading dock: files become specs here, once, and everything shares them.

    from blitspersecond.resources import ResourceManager, load_image_spec

`ResourceManager` is the memo-cache singleton: `.image(path)` -> `ImageSpec`,
`.sound(path)` -> `SoundSpec`, `.sprite_sheet(path)` -> `SpriteSheetSpec`,
`.tmx(path)` -> `TiledFile`,
each loaded and validated exactly once and shared read-only from then on
(PixelBuffer copies image data out; the audio `PCM` operator reads sound data in
place). This is the bottom of the dependency stack -- display -> graphics ->
resources -- so everything above consumes these types and nothing here knows
they exist.

The specs are pure value objects, no GL, no device state: `ImageSpec` (size,
mode P/RGB/RGBA, pixels; indexed transparency must sit at palette index 0),
`SoundSpec` (decoded samples at native rate) and `SpriteSheetSpec` (a whole
sprite package -- tilesets, assemblies, animations -- loaded from the
interchange JSON with every format invariant checked; see sprite_sheet.py, and
`grid_sprite_sheet_spec` there for the package-free path that reads a plain
indexed image as a uniform grid).
`TiledFile` is the corresponding GL-free reading of a BPS-compatible TMX:
sheet descriptions plus structured NumPy planes of local tile indices and
orientation, laid out as `PLANE_DTYPE`. Tiled is an external authoring tool,
so the boundary is here -- the parser reads TMX's own attribute names into
locals, but what it builds is BPS (`cols`/`rows` for cells, `w`/`h` for one
tile in pixels). Nothing above this layer should meet TMX vocabulary.
`Palette` now lives in `blitspersecond.colors` (colour vocabulary, not loading)
and is re-exported here for the specs that carry it. `load_image_spec(path)`
/ `load_sprite_sheet_spec(path)` / `load_tiled_file(path)` are the direct
loaders when you want a spec without cache identity.

The appendix, `resources.internal`, holds `Resource` -- the cached text file
behind `ResourceManager.fetch()`, which exists so `core.Shader` can pull
.vert/.frag source; user code never meets it.
"""

from .resource_manager import ResourceManager
# Palette moved down into blitspersecond.colors (colour vocabulary, not loading);
# re-exported here so `from blitspersecond.resources import Palette` still works.
from blitspersecond.colors import ConsolePalette, Palette
from .image import ImageSpec, load_image_spec
from .sound import SoundSpec
from .sprite_sheet import (
    SpriteSheetSpec,
    grid_sprite_sheet_spec,
    load_sprite_sheet_spec,
)
from .tmx import (
    PLANE_DTYPE,
    TiledFile,
    TiledLayer,
    TiledTileset,
    load_tiled_file,
)

__all__ = [
    "ResourceManager",
    "Palette",
    "ConsolePalette",
    "ImageSpec",
    "load_image_spec",
    "SoundSpec",
    "SpriteSheetSpec",
    "grid_sprite_sheet_spec",
    "load_sprite_sheet_spec",
    "TiledTileset",
    "PLANE_DTYPE",
    "TiledFile",
    "TiledLayer",
    "load_tiled_file",
]
