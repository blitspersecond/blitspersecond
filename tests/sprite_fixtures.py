"""Shared sprite test fixture: a hand-built package with KNOWN pixels.

Tile "a" is a solid 8x8 block, tile "b" is a lone pixel at its top-left
corner, so silhouette answers are predictable to the pixel. "solid" carries
two named rect planes (hit: the whole block, hurt: the centre 4x4); "dot"
and "blank" carry none.
"""

import json
from pathlib import Path

import numpy as np
from PIL import Image

from blitspersecond.graphics import SpriteSheet
from blitspersecond.resources import load_sprite_sheet_spec


def build_known_sheet(tmp_path: Path) -> SpriteSheet:
    px = np.zeros((16, 16), dtype=np.uint8)
    px[0:8, 0:8] = 1  # tile "a": solid 8x8 block
    px[0, 8] = 1  # tile "b": one pixel at ITS top-left corner
    img = Image.fromarray(px, mode="P")
    img.putpalette([0, 0, 0, 255, 0, 0])
    img.save(tmp_path / "sheet.png", transparency=0)
    data = {
        "version": 1,
        "tilesets": {
            "main": {
                "image": "sheet.png",
                "transparent_index": 0,
                "default_palette": 0,
                "palettes": [["#00000000", "#ff0000ff"]],
                "tiles": {
                    "a": {"source": [0, 0, 8, 8]},
                    "b": {"source": [8, 0, 8, 8]},
                },
            }
        },
        "assemblies": {
            "solid": {
                "parts": [
                    {"tileset": "main", "tile": "a", "offset": [0, 0], "order": 0}
                ],
                "collision": {
                    "hit": [[0, 0, 8, 8]],
                    "hurt": [[2, 2, 4, 4]],
                },
            },
            "dot": {
                "parts": [
                    {"tileset": "main", "tile": "b", "offset": [0, 0], "order": 0}
                ]
            },
            "dot-off": {
                # The same lone pixel, offset from the anchor -- overlay
                # tests need geometry that escapes the anchor cross.
                "parts": [
                    {"tileset": "main", "tile": "b", "offset": [4, 0], "order": 0}
                ]
            },
            "blank": {"parts": []},
        },
        "animations": {
            # Tick-precise clip for Animation tests: frame changes fall at
            # cumulative ticks 2, 3, and the loop/done boundary at 6.
            "cycle": {
                "frames": [
                    {"assembly": "solid", "duration_ticks": 2},
                    {"assembly": "dot", "duration_ticks": 1},
                    {"assembly": "blank", "duration_ticks": 3},
                ]
            }
        },
    }
    path = tmp_path / "pack.sprite.json"
    path.write_text(json.dumps(data))
    return SpriteSheet(load_sprite_sheet_spec(str(path)))
