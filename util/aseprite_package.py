#!/usr/bin/env python3
"""Compile an Aseprite document into a BPS sprite package.

    util/aseprite_package.py assets/fumiko/fumiko.aseprite

Aseprite is the authoring surface, never the runtime representation. This is
the import direction: a `.aseprite` file becomes the indexed PNG plus the
`.sprite.json` the engine loads.

Aseprite's own CLI does the packing, and does it better than we would --
`--sheet` writes a strip that preserves INDEXED mode and the transparent
index, and `--data` describes every frame, tag and slice. So there is no lua
in here: the tool is the tool, and this is the part it cannot know about,
which is what the three maps mean.

    tag                 -> animation      ("F Attack" -> "f-attack")
    frame in a tag      -> assembly       ("f-attack-0" ...)
    frame duration      -> duration_ticks (ms x fps / 1000; 100ms @60 = 6)
    layer               -> part
    cel position        -> part offset
    document palette    -> the tileset's one palette table

WHAT IT DOES NOT DO, and why.

Authored collision planes. Not a gap to be filled later by reading Aseprite
slices -- a decision (2026-08-20): real hitbox authoring wants a purpose-built
tool, and it gets built when a game actually demands one, not speculatively.
The two plane sources that exist are enough until then: rects extracted from a
real game (the Chun-Li package), and `--plane`, which puts one naive box over
the drawn extent so the named-plane machinery can be exercised at all.

One palette, because a document has one. Fumiko's four hair colours were
folded in from separate PNGs by hand, and a second pass over `palette_index` is how
that is done.

Untagged frames named `frame-N`. An authoring document says what a run of
frames MEANS by tagging it; inventing a better name from nothing is not the
tool's business.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image

#: One game tick at the engine's default rate. Aseprite stores milliseconds
#: because it previews on a wall clock; packages store ticks because games
#: run on a fixed step, and the conversion is the tool's job: milliseconds are
#: a preview and export concern and never reach the runtime.
DEFAULT_FPS = 60


def slug(name: str) -> str:
    """An Aseprite tag name as a package key: 'F Attack' -> 'f-attack'."""
    return re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")


def export(document: Path, into: Path) -> Tuple[Path, Dict[str, Any]]:
    """Run Aseprite headless: packed sheet + frame/tag/slice data.

    `--sheet-type horizontal` keeps frames in authored order along a strip,
    so the source rects come out in a readable layout rather than whatever a
    bin-packer chose. Indexed mode and the transparent index survive.

    Slices are deliberately not requested: planes are not authored here.
    """
    sheet = into / f"{document.stem}.png"
    data = into / f"{document.stem}.json"
    try:
        subprocess.run(
            [
                "aseprite", "-b", str(document),
                "--sheet", str(sheet),
                "--sheet-type", "horizontal",
                "--data", str(data),
                "--format", "json-array",
                "--list-tags", "--list-layers",
            ],
            check=True,
            capture_output=True,
        )
    except FileNotFoundError:
        raise SystemExit(
            "aseprite not found on PATH -- it is the authoring tool and this "
            "is its import path; there is nothing to fall back to"
        )
    except subprocess.CalledProcessError as error:
        raise SystemExit(
            f"aseprite failed on {document.name}:\n"
            f"{error.stderr.decode(errors='replace')}"
        )
    return sheet, json.loads(data.read_text())


def palette_of(sheet: Path) -> List[str]:
    """The sheet's embedded table as package colour literals.

    Alpha is all-or-nothing by design and index 0 is the transparent one, so
    the table is written exactly as the loader demands rather than as the PNG
    happens to store it.
    """
    image = Image.open(sheet)
    if image.mode != "P":
        raise SystemExit(
            f"{sheet.name} came out mode {image.mode!r} -- a sprite tileset is "
            "an indexed surface; the document must be in Indexed color mode"
        )
    raw = image.getpalette() or []
    entries = len(raw) // 3
    return [
        "#{:02x}{:02x}{:02x}{}".format(
            raw[i * 3], raw[i * 3 + 1], raw[i * 3 + 2], "00" if i == 0 else "ff"
        )
        for i in range(entries)
    ]


def frame_names(data: Dict[str, Any]) -> List[str]:
    """One name per frame: `<tag>-<n>` inside a tag, `frame-<n>` outside one.

    Tags are the document's own statement of what a run of frames means, so
    they are the only naming authority here. Aseprite's json-array `from`/`to`
    are 0-based frame indices.
    """
    names: List[Optional[str]] = [None] * len(data["frames"])
    for tag in data["meta"].get("frameTags", []):
        clip = slug(tag["name"])
        for offset, frame in enumerate(range(tag["from"], tag["to"] + 1)):
            if 0 <= frame < len(names):
                names[frame] = f"{clip}-{offset}"
    return [name or f"frame-{i}" for i, name in enumerate(names)]


def build(
    document: Path,
    fps: int,
    tileset: str,
    ox: int,
    oy: int,
    plane: Optional[str],
) -> Tuple[Image.Image, Dict[str, Any]]:
    with tempfile.TemporaryDirectory() as tmp:
        sheet, data = export(document, Path(tmp))
        palette = palette_of(sheet)
        # Read the pixels out before the directory goes: everything below is
        # arithmetic over `data`, and the caller wants an image, not a path
        # into somewhere that no longer exists.
        packed = Image.open(sheet)
        packed.load()

    layers = data["meta"].get("layers", [])
    if len(layers) > 1:
        raise SystemExit(
            f"{document.name} has {len(layers)} layers. One layer is one part, "
            "and a packed sheet flattens them -- multipart documents need a "
            "per-layer export, which this tool does not do yet. That is for "
            "art STACKED IN PLACE, like Chun-Li's character and effect layers "
            "in one frame; a wardrobe laid out side by side on one canvas is "
            "an ordinary tileset and imports fine."
        )

    names = frame_names(data)
    tiles: Dict[str, Any] = {}
    assemblies: Dict[str, Any] = {}
    for name, frame in zip(names, data["frames"]):
        rect = frame["frame"]
        # The cel's placement inside the canvas, so a trimmed export still
        # lands where it was drawn; ox/oy then move the anchor for the whole
        # sheet (feet-centred art is `-w // 2`, `-h`).
        source = frame.get("spriteSourceSize", {"x": 0, "y": 0})
        tiles[name] = {"source": [rect["x"], rect["y"], rect["w"], rect["h"]]}
        x, y = source.get("x", 0) + ox, source.get("y", 0) + oy
        assembly: Dict[str, Any] = {
            "parts": [
                {
                    "tileset": tileset,
                    "tile": name,
                    "offset": [x, y],
                    "order": 0,
                }
            ]
        }
        if plane:
            # One box over the drawn extent. Real hitboxes are authored as
            # Aseprite slices; this is the stand-in that lets the plane path
            # be exercised before anyone has drawn any.
            assembly["collision"] = {plane: [[x, y, rect["w"], rect["h"]]]}
        assemblies[name] = assembly

    animations: Dict[str, Any] = {}
    for tag in data["meta"].get("frameTags", []):
        frames = []
        for frame in range(tag["from"], tag["to"] + 1):
            ms = data["frames"][frame]["duration"]
            ticks = max(1, round(ms * fps / 1000))
            if abs(ms * fps / 1000 - ticks) > 0.01:
                print(
                    f"  note: {names[frame]} holds {ms}ms, which is not a whole "
                    f"tick at {fps}fps -- rounded to {ticks}",
                    file=sys.stderr,
                )
            frames.append({"assembly": names[frame], "duration_ticks": ticks})
        animations[slug(tag["name"])] = {"frames": frames}

    package = {
        "version": 1,
        "tick_unit": "integer simulation tick",
        "tilesets": {
            tileset: {
                "image": f"{document.stem}.png",
                "transparent_index": 0,
                "default_palette": 0,
                "palettes": [palette],
                "tiles": tiles,
            }
        },
        "assemblies": assemblies,
        "animations": animations,
    }
    return packed, package


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("document", type=Path, help="the .aseprite file")
    parser.add_argument(
        "-o", "--out", type=Path,
        help="directory for the .png and .sprite.json (default: alongside)",
    )
    parser.add_argument("--fps", type=int, default=DEFAULT_FPS)
    parser.add_argument(
        "--tileset", default="character", help="name for the one tileset"
    )
    parser.add_argument("--ox", type=int, default=0, help="anchor shift, x")
    parser.add_argument("--oy", type=int, default=0, help="anchor shift, y")
    parser.add_argument(
        "--plane",
        help="emit a full-extent AABB under this plane name (test stand-in "
             "for authored slices)",
    )
    args = parser.parse_args()

    document = args.document.resolve()
    out = (args.out or document.parent).resolve()
    out.mkdir(parents=True, exist_ok=True)

    packed, package = build(
        document, args.fps, args.tileset, args.ox, args.oy, args.plane
    )
    image = out / f"{document.stem}.png"
    packed.save(image, transparency=0, optimize=False)
    target = out / f"{document.stem}.sprite.json"
    target.write_text(json.dumps(package, indent=2) + "\n")

    tileset = package["tilesets"][args.tileset]
    print(
        f"{document.name} -> {target.name} + {image.name}\n"
        f"  {len(tileset['tiles'])} tiles, {len(package['assemblies'])} "
        f"assemblies, {len(package['animations'])} animations: "
        f"{', '.join(sorted(package['animations']))}"
    )


if __name__ == "__main__":
    main()
