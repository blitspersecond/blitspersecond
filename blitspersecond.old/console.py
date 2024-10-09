import numpy as np
from .tile import Tile
from .config import Config
from typing import Tuple
from .constants import *
import importlib.resources as pkg_resources
from blitspersecond import resources
from blitspersecond.imagemap import ImageMap

# sort out color / palette stuff - consistency between console, console text, and tile
# also normalise rgba / index stuff


class Console(Tile):
    def __init__(self):
        self._height = Config().window.height  # 360
        self._width = Config().window.width  # 640
        self._tile_height = 12
        self._tile_width = 8

        rgba_console = np.zeros((self._height, self._width, 4), dtype=np.uint8)
        tile = "resources/ascii12x8.png"  # Assuming this is the relative path to the tile image
        tileset = ImageMap(tile, self._tile_width, self._tile_height)

        # Initialize the base Tile class with the console buffer and palette
        super().__init__(rgba_console, BPS_DEFAULT_PALETTE)

        # Create ConsoleText based on the number of tiles that fit on the screen
        self._console_text = ConsoleText(
            self._width // self._tile_width, self._height // self._tile_height
        )

    def write(self, text: str):
        """Proxy to ConsoleText.write()."""
        self._console_text.write(text)

    @property
    def palette(self) -> Tuple[int, int, int]:
        """Proxy to ConsoleText.palette property."""
        return self._console_text.palette

    @palette.setter
    def palette(self, color_tuple: Tuple[int, int, int]):
        """Proxy to ConsoleText.palette setter."""

        self._console_text.palette = color_tuple

    @property
    def cursor_position(self) -> Tuple[int, int]:
        """Proxy to ConsoleText.cursor_position property."""
        return self._console_text.cursor_position

    @cursor_position.setter
    def cursor_position(self, position: Tuple[int, int]):
        """Proxy to ConsoleText.cursor_position setter."""
        self._console_text.cursor_position = position

    def clear(self):
        """Proxy to ConsoleText.clear() to reset the console."""
        self._console_text.clear()

    def set_char(self, char: str):
        """Proxy to ConsoleText.set_char() to set the character at the cursor."""
        self._console_text.set_char(char)

    def scroll_up(self):
        """Proxy to ConsoleText.scroll_up() to scroll the console contents up."""
        self._console_text.scroll_up()


class ConsoleText(object):
    def __init__(self, width, height):
        self._buffer = ""
        self.width = 80
        self.height = 30
        self._console = np.zeros((self.height, self.width, 4), dtype=np.uint8)
        self._cursor_x = 0
        self._cursor_y = 0
        self._bg_color = BPS_COLOR_BLACK
        self._fg_color = BPS_COLOR_WHITE
        self._outline_color = BPS_COLOR_TRANSPARENT

    @property
    def color(self) -> Tuple[int, int, int]:
        return self._bg_color, self._fg_color, self._outline_color

    @color.setter
    def color(self, color_tuple: Tuple[int, int, int]):
        self._bg_color, self._fg_color, self._outline_color = color_tuple

    @property
    def cursor_position(self):
        return self._cursor_x, self._cursor_y

    @cursor_position.setter
    def cursor_position(self, position: Tuple[int, int]):
        new_x, new_y = position
        # Check if the new position is out of bounds
        if not (0 <= new_x < self.width and 0 <= new_y < self.height):
            raise ValueError(f"Cursor position out of bounds: ({new_x}, {new_y})")
        self._cursor_x, self._cursor_y = new_x, new_y

    @property
    def char(self) -> Tuple[int, int, int, int]:
        char = self._console[self._cursor_y][self._cursor_x]
        return (char[0], char[1], char[2], char[3])

    @char.setter
    def char(
        self,
        char: str,
        background: int = None,
        foreground: int = None,
        outline: int = None,
    ):
        # Use current palette if not specified
        fg_color = foreground if foreground is not None else self._fg_color
        bg_color = background if background is not None else self._bg_color
        ol_color = outline if outline is not None else self._outline

        # Set the character and its attributes in the console
        self._console[self._cursor_y][self._cursor_x] = (
            ord(char),
            fg_color,
            ol_color,
            bg_color,
        )

        # Advance the cursor position: TODO: Do we want this behaviour?
        self._advance_cursor()

    @char.setter
    def write(self, text):
        self._buffer += text
        while len(self._buffer) > 0:
            char = self._pop()
            if self._is_printable(char):
                self._console[self._cursor_y][self._cursor_x] = (
                    ord(char),
                    self._fg_color,
                    self._bg_color,
                    self._outline_color,
                )
                self._advance_cursor()
            elif char == "\n":
                self._newline()

    def _pop(self):
        char = self._buffer[0]
        self._buffer = self._buffer[1:]
        return char

    def _advance_cursor(self):
        self._cursor_x += 1
        if self._cursor_x >= self.width:
            self._newline()

    def _newline(self):
        self._cursor_x = 0
        self._cursor_y += 1
        if self._cursor_y >= self.height:
            self._cursor_y = self.height - 1
            self._scroll_up()

    def _scroll_up(self):
        self._console[:-1] = self._console[1:]
        self._console[-1] = (
            0x0,
            self._fg_color,
            self._bg_color,
            self._outline_color,
        )

    def _is_printable(self, char):
        return 32 <= ord(char) <= 127

    def __str__(self) -> str:
        result = ""
        for row in range(self.height):
            for col in range(self.width):
                char_code = self._console[row, col, 0]
                if char_code > 0:
                    result += chr(char_code)
            result += "\n"
        return result
