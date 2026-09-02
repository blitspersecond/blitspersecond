"""Colour: the palette table and the names that index it.

    from blitspersecond.graphics import Palette, RED, system_palette

`Palette` is the 256-entry RGBA table indexed images carry and the engines
upload -- stored as authored, index 0 always transparent (`ConsolePalette` is
the one sanctioned exception, honouring per-entry alpha for the console's
stunts). `system_palette()` builds the stock 16-colour map, and the constants
(`RED`, `WHITE`, `CYAN`, ...) are its indices -- write `console.pen[1] = RED`
and mean it.

`PaletteBank` is several of those under one owner -- the tables a sprite
tileset carries -- held together because they reach the GPU as ONE 256xN
texture a draw picks a row out of, rather than as N textures it binds
between.

**A colour in BPS is an index, not a triple.** That is the whole reason this
sits in `graphics` rather than beside the framebuffer: resolving an index to
a colour needs a palette bound, which only the engines have. By the time a
frame reaches the window system it is already resolved RGBA -- the CRT
post-pass samples a full-colour texture and has no palette uniform at all --
so nothing down there imports this.

This was `blitspersecond.colors`, a two-module package of its own. The split
put the table in one file and its row numbers in the other, which is one
subject wearing two names: the constants are indices INTO a `Palette`, and
`system_palette()` returns one. Two hundred lines did not need a package
boundary drawn through the middle of them.
"""

from numpy import uint8, zeros
from typing import Iterable, Iterator, List, Sequence, Tuple, Optional, Union

# The system palette: the 16 basic colours every machine since CGA has agreed
# on -- the HTML 4.01 / VGA set, ratified 1999, known to every kid who ever
# typed a hex code. The engine's default colour map, and not just the
# console's: a colour is a palette index, so these name sprite and tile
# colours too.
#
# Index 0 is the transparent hole (the engine-wide empty index; a Palette pins
# it transparent whatever its RGB), so the 16 colours live at 1..16 and BLACK
# is a real index, not the hole.
#
# Spelled out (not looped) so an editor resolves each name and a kid can read
# the whole map at a glance.
TRANSPARENT = 0
WHITE = 1
SILVER = 2
GRAY = 3
BLACK = 4
RED = 5
MAROON = 6
YELLOW = 7
OLIVE = 8
LIME = 9
GREEN = 10
CYAN = 11
TEAL = 12
BLUE = 13
NAVY = 14
MAGENTA = 15
PURPLE = 16

# The RGB behind each index, in index order (index 0's colour is ignored -- the
# Palette pins it transparent). This is the single source the palette is built
# from; the constants above are its row numbers.
_RGB = (
    0x000000,  # 0  transparent hole
    0xFFFFFF,  # 1  white
    0xC0C0C0,  # 2  silver
    0x808080,  # 3  gray
    0x000000,  # 4  black
    0xFF0000,  # 5  red
    0x800000,  # 6  maroon
    0xFFFF00,  # 7  yellow
    0x808000,  # 8  olive
    0x00FF00,  # 9  lime
    0x008000,  # 10 green
    0x00FFFF,  # 11 cyan
    0x008080,  # 12 teal
    0x0000FF,  # 13 blue
    0x000080,  # 14 navy
    0xFF00FF,  # 15 magenta
    0x800080,  # 16 purple
)


def _byte(value: int) -> int:
    """One channel, clamped to a byte. The table stores what you gave it.

    There was an RGB565 quantisation step here until 2026-07-31: every colour
    was snapped to the nearest 5/6/5 level on the way in. It bought nothing --
    the framebuffer is full-colour and nothing downstream reads a 565 word --
    and cost the obvious thing: your art did not come back the colour you
    authored it.
    """
    return int(min(255, max(0, int(value))))


class Palette:
    def __init__(self, pal: Optional[Sequence[int]] = None) -> None:
        self._palette = zeros((256, 4), dtype=uint8)  # 256 colors, 4 channels (RGBA)
        # Mutation counter: the GPU projection (graphics.internal's
        # PaletteTexture, palette RAM as a texture) re-uploads the table this
        # palette is a row of iff this moved -- so a write here is just the
        # array store plus one increment, and the upload happens at most once
        # per changed palette per frame, at the next draw that needs it.
        self._version = 0

        # Default to black for all colors if no palette provided
        if pal is None:
            # Set all colors to black with full alpha (except index 0)
            self._palette[:, 3] = 255  # Set alpha to 255 for all colors
            self._palette[0, 3] = 0  # Transparent for index 0
            return

        for i in range(0, len(pal), 3):
            idx = i // 3
            # Plain ints, deliberately: wrapping these in uint8() to store them
            # into a uint8 array costs the wrap AND makes numpy's row assignment
            # 3x slower (1.67us vs 0.52us) for no gain.
            self._palette[idx] = [
                _byte(pal[i]),
                _byte(pal[i + 1]),
                _byte(pal[i + 2]),
                0 if idx == 0 else 255,
            ]

    def raw(self) -> List[int]:
        """The table as a flat list of RGB triples -- what `Palette(...)`
        takes, so `Palette(p.raw())` round-trips.

        RGB, not RGBA, despite holding an RGBA array: alpha is not authored
        data here, it is derived from the index by `_alpha`, so a constructor
        that read it back would be reading a value nobody wrote.
        """
        raw = []
        for i in range(256):
            color = self._palette[i][:3]  # Get RGBA components
            raw.extend(int(channel) for channel in color)
        return raw

    def __getitem__(self, key: int) -> Tuple[int, int, int, int]:
        r, g, b, a = self._palette[key]
        return int(r), int(g), int(b), int(a)

    def __setitem__(
        self,
        key: int,
        value: Union[Tuple[int, int, int], Tuple[int, int, int, int], List[int]],
    ) -> None:
        """Write one entry, as `(r, g, b)` or `(r, g, b, a)`.

        THREE CHANNELS IS THE NORMAL SPELLING. Alpha is not information the
        caller is expected to have: the index decides it, by the engine-wide
        rule that 0 is the hole and everything above it is solid. Omitting it
        asks for `255`, and `_alpha` then applies the rule -- so a base
        Palette lands on the same entry either way, and only ConsolePalette,
        which honours what it is passed, can tell the two apart. There it
        reads as "fully visible", which is what you meant by not saying.

        Raises on anything else. This used to be `if isinstance(...) and
        len(value) == 4:` with no else, so `palette[5] = (255, 0, 0)` -- RGB
        without alpha, the likeliest mistake there is -- wrote nothing and
        said nothing, leaving the entry black. A palette that silently
        refuses a colour is found by staring at the art, not at the error, so
        the two plausible shapes are accepted and the rest is an error.
        """
        if not isinstance(value, (list, tuple)) or len(value) not in (3, 4):
            raise TypeError(
                f"palette entry must be (r, g, b) or (r, g, b, a); "
                f"got {value!r}"
            )
        red, green, blue = value[0], value[1], value[2]
        requested = value[3] if len(value) == 4 else 255
        self._palette[key] = [
            _byte(red),
            _byte(green),
            _byte(blue),
            self._alpha(key, requested),
        ]
        self._version += 1

    def _alpha(self, key: int, requested: int) -> int:
        """The alpha law: a palette is an index->opaque-colour table plus the
        single transparent index 0. The requested alpha is deliberately
        ignored -- fades come from elsewhere (blend registers, dither, RGBA
        parts). ConsolePalette is the one sanctioned exception."""
        return 0 if key == 0 else 255

    @property
    def version(self) -> int:
        """Monotonic mutation counter -- how the GPU projection knows this
        table changed without diffing 256 entries."""
        return self._version

    def tobytes(self) -> bytes:
        """The table as its raw 1KB RGBA row -- what PaletteTexture uploads.
        A copy, taken only when an upload actually happens (version-gated)."""
        return self._palette.tobytes()


class ConsolePalette(Palette):
    """A Palette that breaks the alpha law, on purpose and in quarantine:
    __setitem__ honours the alpha you pass, exactly. Built for the
    quadrant-font console's fake-alpha stunts (cloud decks, low-res blend
    abuse) -- hand THIS to the engine doing the abusing and nothing else: the
    base Palette's all-or-nothing rule still governs sprites, tiles and every
    loaded asset.

    Alpha was quantised to 4 bits here until 2026-07-31, to match the RGB565
    step the rest of the table was taking. That step is gone, and with it the
    only argument for this one.

    Index 0 stays pinned fully transparent regardless: it is the engine-wide
    hole index (masks and collision solidity key off index 0, not alpha), so
    it is structural, not part of the rule being broken."""

    def _alpha(self, key: int, requested: int) -> int:
        return 0 if key == 0 else _byte(requested)


class PaletteBank:
    """The palettes ONE tileset carries, held as one table.

    An ordinary indexed image has a single palette. A sprite tileset has a
    bank of them -- four for fumiko, twenty-eight for Chun-Li -- and which one
    a presentation draws through is a register it picks at runtime.

    They are an object rather than a tuple because the bank is the unit the
    GPU stores: `graphics.internal.PaletteTexture` projects one of these to a
    single 256xN RGBA texture, and the shader picks the row. Selecting a
    palette is then a number, not a bind, which is what lets it become
    per-quad stream data later. A tuple could not own that projection: the
    memo is weak-keyed so that a dead owner releases its texture, and a tuple
    cannot be weakly referenced.

    It reads as the tuple it replaced -- `len(bank)`, `bank[3]`, iteration --
    because the register a game writes IS the row, and nothing that only
    wanted a table needs to know it now sits in one.
    """

    __slots__ = ("_palettes", "__weakref__")

    def __init__(self, palettes: Iterable[Palette]) -> None:
        self._palettes: Tuple[Palette, ...] = tuple(palettes)
        if not self._palettes:
            raise ValueError(
                "a palette bank holds at least one palette -- an indexed "
                "image read through no table is unreadable"
            )
        for index, palette in enumerate(self._palettes):
            if not isinstance(palette, Palette):
                raise TypeError(
                    f"palette bank row {index}: expected Palette, got "
                    f"{type(palette).__name__}"
                )

    def __len__(self) -> int:
        return len(self._palettes)

    def __getitem__(self, row: int) -> Palette:
        """Row `row`'s table. The index is the palette register a
        presentation selects with, and out of range is an IndexError -- the
        register setter checks the bound, so reaching here with a bad one is
        a bug rather than a game's mistake."""
        return self._palettes[row]

    def __iter__(self) -> Iterator[Palette]:
        return iter(self._palettes)

    def __repr__(self) -> str:
        return f"<PaletteBank {len(self._palettes)} palettes>"


def system_palette() -> Palette:
    """The stock 16-colour palette (index 0 transparent, 1..16 the basic
    colours). The `palette=None` default for GlyphEngine and Console builds
    this, so a bare console writes in real, named colours."""
    raw: list[int] = []
    for rgb in _RGB:
        raw += [(rgb >> 16) & 0xFF, (rgb >> 8) & 0xFF, rgb & 0xFF]
    return Palette(raw)
