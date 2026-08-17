from numpy import uint8, zeros
from typing import List, Sequence, Tuple, Optional, Union


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
        # PaletteTexture, palette RAM as a 256x1 texture) re-uploads a
        # palette's row iff this moved -- so a write here is just the array
        # store plus one increment, and the upload happens at most once per
        # changed palette per frame, at the next draw that needs it.
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
        # Returns a flattened list of RGBA values suitable for creating a new palette.
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
        value: Union[Tuple[int, int, int, int], List[int]],
    ) -> None:
        if isinstance(value, (list, tuple)) and len(value) == 4:
            r, g, b, a = value
            entry = [
                _byte(r),
                _byte(g),
                _byte(b),
                self._alpha(key, a),
            ]
            self._palette[key] = entry
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
