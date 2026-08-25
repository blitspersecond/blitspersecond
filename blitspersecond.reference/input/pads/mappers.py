"""Named mappers: which pad is this, and what does it call its buttons?

The duck test does the heavy lifting -- an unknown pad IS an Xbox pad, because
the 360 is where pads converge (see docs/CONTROLLERS.md, "The idealised pad").
So this table is NOT a gate. Nothing here rejects a pad; a pad that matches
nothing falls through to XboxMapper and is perfectly happy there. The table's
job is narrower and entirely additive:

  * NAME. /proc gives us the kernel's device string ("Sony Interactive
    Entertainment Wireless Controller"); the table gives us the name a person
    would use ("DualShock 4").
  * LABELS. FACE_SOUTH is a physical position -- correct, and useless to a HUD
    that wants to draw "Press X". The mapper knows the pad prints an X there,
    a Sony pad prints a cross, and a Nintendo pad prints a B.
  * OVERRIDES, if a pad ever earns one. Every pad validated so far (Hori OCTA,
    DualShock 4, DualSense, Xbox Wireless) reads correctly on the standard
    button map, so no mapper overrides BUTTON_MAP today. The seam exists for
    the pad that eventually needs it; it is deliberately not populated in
    advance.

Selection is two-tier, so a pad we have never seen still lands somewhere
sensible: an exact vendor:product can refine the mapper; failing that a KNOWN
VENDOR picks that vendor's mapper (an unreleased Sony pad still says "cross",
because that is a fact about Sony, not about the model); failing that, Xbox.
Editorial model names are independent: the Hori OCTA has a known name while
deliberately exercising the ordinary Xbox fallback mapper.

Labels are the vendor's PHYSICAL truth -- what is printed on the plastic.
`ACCEPT` is the one family convention shared across games: south for
Xbox/Sony, east for Nintendo. Games remain free to ask for physical FACE
positions directly.

The vendor and product ids below were deeply researched, NOT scraped. Do not
"improve" them by pasting in SDL_GameControllerDB or any other community
controller-db: that is the reflex answer and it is the wrong one here.
"""

from __future__ import annotations

from .codes import (
    ABS_RZ,
    ABS_Z,
    BTN_EAST,
    BTN_NORTH,
    BTN_SELECT,
    BTN_SOUTH,
    BTN_START,
    BTN_TL,
    BTN_TR,
    BTN_WEST,
    Button,
)

# BTN_THUMBL/BTN_THUMBR -- the stick-clicks -- appear in no BUTTON_MAP below,
# on purpose. A game cannot bind what it cannot name. See codes.Button.


class PadMapper:
    """A pad family: its button codes, and what the vendor prints on them.

    Stateless -- used as a namespace, never instantiated. Subclass to name a
    family; override BUTTON_MAP only if a pad genuinely wires its buttons
    differently (none do, so far).
    """

    #: evdev button code -> Button. The Xbox layout, by physical position.
    BUTTON_MAP: dict[int, Button] = {
        BTN_SOUTH: Button.FACE_SOUTH,
        BTN_EAST: Button.FACE_EAST,
        BTN_WEST: Button.FACE_WEST,
        BTN_NORTH: Button.FACE_NORTH,
        BTN_TL: Button.LB,
        BTN_TR: Button.RB,
        BTN_SELECT: Button.SELECT,
        BTN_START: Button.START,
    }

    #: Trigger axis code -> the Button it thresholds into.
    TRIGGER_MAP: dict[int, Button] = {ABS_Z: Button.LT, ABS_RZ: Button.RT}

    #: This family's conventional confirm position.
    ACCEPT = Button.FACE_SOUTH

    #: Button -> the label printed on the plastic. For HUDs, not for meaning.
    LABELS: dict[Button, str] = {}

    @classmethod
    def label(cls, button: Button) -> str:
        """What this pad prints on `button` (e.g. 'A', 'cross', 'ZR')."""
        return cls.LABELS.get(button, button.name)


class XboxMapper(PadMapper):
    """Microsoft pads -- and every pad that merely looks like one, which is
    most of them. This is the duck test's landing spot, so it is also the
    fallback for pads matching nothing in the table."""

    LABELS = {
        Button.FACE_SOUTH: "A",
        Button.FACE_EAST: "B",
        Button.FACE_WEST: "X",
        Button.FACE_NORTH: "Y",
        Button.LB: "LB",
        Button.RB: "RB",
        Button.LT: "LT",
        Button.RT: "RT",
        Button.START: "Menu",
        Button.SELECT: "View",
    }


class SonyMapper(PadMapper):
    """PlayStation pads. Shapes, not letters; L1/L2 rather than LB/LT."""

    LABELS = {
        Button.FACE_SOUTH: "cross",
        Button.FACE_EAST: "circle",
        Button.FACE_WEST: "square",
        Button.FACE_NORTH: "triangle",
        Button.LB: "L1",
        Button.RB: "R1",
        Button.LT: "L2",
        Button.RT: "R2",
        Button.START: "Options",
        Button.SELECT: "Share",
    }


class DualSenseMapper(SonyMapper):
    """PS5 pads: the DualShock 4's Share button became Create."""

    LABELS = SonyMapper.LABELS | {Button.SELECT: "Create"}


class NintendoMapper(PadMapper):
    """Nintendo pads. The letters are the SAME FOUR as Xbox, rotated: A sits
    east and B sits south, X north and Y west. This is exactly why Button names
    positions rather than letters -- Button.FACE_SOUTH is the bottom button on
    every pad ever made, and here the vendor happens to print a B on it."""

    ACCEPT = Button.FACE_EAST

    LABELS = {
        Button.FACE_SOUTH: "B",
        Button.FACE_EAST: "A",
        Button.FACE_WEST: "Y",
        Button.FACE_NORTH: "X",
        Button.LB: "L",
        Button.RB: "R",
        Button.LT: "ZL",
        Button.RT: "ZR",
        Button.START: "+",
        Button.SELECT: "-",
    }


# -- the selector ------------------------------------------------------------
# Keys are "vendor:product", lowercase hex, exactly as /proc/bus/input/devices
# prints them ("I: Bus=0005 Vendor=054c Product=0ce6").
#
# Steam (0x28de) is absent on purpose and must stay absent: its VIRTUAL pads
# lie about identity (empty Uniq AND empty Phys, shared product id, and they
# multiply), so there is nothing to fingerprint and no stable Pad ID to reclaim
# to. The evdev scanner rejects the vendor before we ever get here.

_VENDORS: dict[str, type[PadMapper]] = {
    "045e": XboxMapper,  # Microsoft
    "054c": SonyMapper,  # Sony
    "057e": NintendoMapper,  # Nintendo
}

_MODELS: dict[str, str] = {
    # Microsoft
    "045e:028e": "Xbox 360 Controller",
    "045e:0291": "Xbox 360 Controller",
    "045e:02a1": "Xbox 360 Wireless Receiver",
    "045e:02d1": "Xbox One Controller",
    "045e:02dd": "Xbox One Controller",
    "045e:02e0": "Xbox One Controller",
    "045e:02e3": "Xbox One Controller",
    "045e:02ea": "Xbox One Controller",
    "045e:02fd": "Xbox One Controller",
    "045e:02ff": "Xbox One Controller",
    "045e:0719": "Xbox 360 Controller",
    "045e:0b00": "Xbox Elite Series 2",
    "045e:0b05": "Xbox Elite Series 2",
    "045e:0b0a": "Xbox Adaptive Controller",  # Accessibility
    "045e:0b0c": "Xbox One Controller",
    "045e:0b12": "Xbox One Controller",
    "045e:0b13": "Xbox Series Controller",
    "045e:0b20": "Xbox One Controller",
    "045e:0b22": "Xbox Wireless Controller",
    # Sony
    "054c:05c4": "DualShock 4",
    "054c:09cc": "DualShock 4",
    "054c:0ba0": "DualShock 4 (Rev 2)",
    "054c:0ce6": "DualSense",
    "054c:0df2": "DualSense Edge",
    "054c:0e5f": "PS5 Access Controller",  # Accessibility
    "054c:1337": "PlayStation Vita (USB Debug)",
    # Nintendo
    "057e:0330": "Wii U Pro Controller",
    "057e:2009": "Switch Pro Controller",
    # Hori
    "0f0d:0150": "Hori Fighting Commander (Xbox)",
}

# The PS5 pads are DualSense-labelled (Create, not Share); everything else
# under a vendor takes that vendor's mapper.
_MODEL_MAPPERS: dict[str, type[PadMapper]] = {
    "054c:0ce6": DualSenseMapper,
    "054c:0df2": DualSenseMapper,
    "054c:0e5f": DualSenseMapper,
}


def mapper_for(vendor: str, product: str) -> type[PadMapper]:
    """The mapper for a vendor:product pair, both lowercase hex from /proc.

    Never fails and never rejects: an unrecognised pad gets XboxMapper, which
    is the whole point of the duck test."""
    key = f"{vendor}:{product}".lower()
    if key in _MODEL_MAPPERS:  # a model that differs from its vendor's default
        return _MODEL_MAPPERS[key]
    return _VENDORS.get(vendor.lower(), XboxMapper)


def model_for(vendor: str, product: str) -> str | None:
    """The human name for a vendor:product pair, or None if we don't know it.

    None is not a failure -- it means "no editorial name", and the caller
    falls back to the kernel's device string, which is always present."""
    return _MODELS.get(f"{vendor}:{product}".lower())
