"""Which pad is this, and what does it print on its buttons?

A mapper answers two questions a `Button` cannot: the name a person would use
for this pad, and the label on the plastic under their thumb. `Button` says
FACE_SOUTH; the mapper says the vendor printed 'A' there, or 'cross', or 'B'.

**Nothing here is a compatibility check.** A pad that matches nothing falls
through to `XboxMapper` and works perfectly -- see `mapper_for`. The table only
ever adds a name and a label.

Design record: `bps/reference/input/pads.md`, *Which pad is this, and what
does it print*.
"""

from __future__ import annotations

from .button import Button


class PadMapper:
    """A pad family: what it calls the buttons every pad has.

    Stateless, used as a namespace and never instantiated. Subclass to name a
    family.

    The base labels are the Xbox ones by inheritance rather than by default --
    `XboxMapper` is where they live, because "unlabelled" is not a state any
    real pad is in.
    """

    #: This family's conventional confirm position. South everywhere except
    #: Nintendo, and a game that wants a physical position can always ask for
    #: one directly rather than going through this.
    ACCEPT = Button.FACE_SOUTH

    #: Button -> the label printed on the plastic. For HUDs, never for
    #: meaning: a game binds FACE_SOUTH and *prompts* with whatever this says.
    LABELS: dict[Button, str] = {}

    @classmethod
    def label(cls, button: Button) -> str:
        """What this pad prints on `button` -- 'A', 'cross', 'ZR'.

        Falls back to the button's own name, which is a position and therefore
        always true, if a family has not said otherwise.
        """
        return cls.LABELS.get(button, button.name)


class XboxMapper(PadMapper):
    """Microsoft pads, and every pad that merely looks like one -- which is
    most of them.

    This is the duck test's landing spot and therefore the fallback, and it is
    the fallback because it is the MODAL answer rather than a lowest common
    denominator: a pad nobody has seen is more likely Xbox-shaped than
    anything else, and generics deliberately present as one.
    """

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
    """PlayStation pads. Shapes rather than letters, and L1/L2 not LB/LT."""

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
    """PS5 pads: the DualShock 4's Share button became Create.

    One label different from its parent, and it is the button that has been
    renamed most often by everyone -- see `Button.SELECT`.
    """

    LABELS = SonyMapper.LABELS | {Button.SELECT: "Create"}


class NintendoMapper(PadMapper):
    """Nintendo pads.

    **The letters are the same four as Xbox, rotated**: A sits east and B
    south, X north and Y west. This is the clearest case for why `Button`
    names positions -- FACE_SOUTH is the bottom button on every pad ever made,
    and here the vendor prints a B on it. A vocabulary built on letters would
    have Nintendo's A and Microsoft's A mean different thumbs.
    """

    #: East, not south. The one family that differs, which is why ACCEPT
    #: exists as a mapper question rather than a constant.
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
#
# Keys are "vendor:product", lowercase hex, exactly as /proc/bus/input/devices
# prints them ("I: Bus=0005 Vendor=054c Product=0ce6").
#
# THE TABLES BELOW ARE RESEARCHED, NOT SCRAPED. **Do not "improve" them by
# pasting in SDL_GameControllerDB or any other community controller database.**
# That is the reflex answer and it is the wrong one: those databases are
# BEHAVIOURAL, they collide with themselves, and vendor:product is a CLAIM
# rather than ground truth -- third-party pads report a first-party id because
# that is what gets them working everywhere. A bigger table is not more
# authoritative; it holds more confident answers about ids that several
# unrelated devices assert. The figures are in `bps/reference/input/pads.md`,
# *The tables are researched, not scraped*.
#
# Steam (0x28de) is absent on purpose and must stay absent. The transport
# rejects the vendor before anything reaches here -- see `evdev.py`.

#: vendor -> that vendor's label family. Three, because there are three
#: families a person actually owns: an Xbox pad is what retail sells for a PC,
#: and a DualShock, DualSense or Switch pad is what was already next to a
#: console. Anything else is covered by the fallback.
_VENDORS: dict[str, type[PadMapper]] = {
    "045e": XboxMapper,  # Microsoft
    "054c": SonyMapper,  # Sony
    "057e": NintendoMapper,  # Nintendo
}

#: vendor:product -> a mapper that differs from its vendor's default. The PS5
#: pads are Create-labelled where the PS4 said Share.
_MODEL_MAPPERS: dict[str, type[PadMapper]] = {
    "054c:0ce6": DualSenseMapper,
    "054c:0df2": DualSenseMapper,
    "054c:0e5f": DualSenseMapper,
}

#: vendor:product -> the name a person would use. EDITORIAL ONLY: nothing
#: here decides behaviour, and `mapper_for` never reads it. An error costs one
#: wrong word in a HUD, which is why this table can be researched where the
#: two above want the pad in your hands.
#:
#: One physical pad contributes several ids -- wired, over Bluetooth, through
#: a dongle, and across revisions -- so this describes far fewer than 29
#: devices.
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
    "045e:0b0a": "Xbox Adaptive Controller",
    "045e:0b0c": "Xbox One Controller",
    # 0b12 and 0b13 are one model on two links -- the kernel names 0b12
    # "Microsoft Xbox Series S|X Controller" itself. Corrected 2026-09-14
    # after the pad was plugged in; v2's extraction had them disagreeing.
    "045e:0b12": "Xbox Series Controller",
    "045e:0b13": "Xbox Series Controller",
    "045e:0b20": "Xbox One Controller",
    "045e:0b22": "Xbox Wireless Controller",
    # Sony
    "054c:05c4": "DualShock 4",
    "054c:09cc": "DualShock 4",
    "054c:0ba0": "DualShock 4 (Rev 2)",
    "054c:0ce6": "DualSense",
    "054c:0df2": "DualSense Edge",
    "054c:0e5f": "PS5 Access Controller",
    "054c:1337": "PlayStation Vita (USB Debug)",
    # Nintendo
    "057e:0330": "Wii U Pro Controller",
    "057e:2009": "Switch Pro Controller",
    # Hori
    "0f0d:0150": "Hori Fighting Commander (Xbox)",
}


def mapper_for(vendor: str, product: str) -> type[PadMapper]:
    """The label family for a vendor:product pair, lowercase hex.

    **Never fails and never rejects.** Two tiers and a default: an exact model
    can refine the mapper, failing that a known VENDOR picks that vendor's
    family, failing that `XboxMapper`.

    The vendor tier is the interesting one -- an unreleased Sony pad still
    says "cross", because that is a fact about Sony rather than about the
    model. It is what lets the table stay small and stay right about hardware
    that does not exist yet.
    """
    key = f"{vendor}:{product}".lower()
    if key in _MODEL_MAPPERS:
        return _MODEL_MAPPERS[key]
    return _VENDORS.get(vendor.lower(), XboxMapper)


def model_for(vendor: str, product: str) -> str | None:
    """The name a person would use for this pad, or None if we do not know it.

    None is not a failure. It means "no editorial name", and the caller falls
    back to the transport's own device string, which is always present.
    """
    return _MODELS.get(f"{vendor}:{product}".lower())
