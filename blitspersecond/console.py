import importlib.resources as pkg_resources
from pathlib import Path
from typing import Tuple
from .tile import Tile
from .config import Config
from .imagemap import ImageMap
from .palette import Palette
import blitspersecond.resources


class Console:
    def __init__(
        self, file=Config().console.tileset, tilesize: Tuple[int, int] = (12, 8)
    ):
        tileset_path = self._get_tileset_path("ascii12x8.png")
        self.tileset = ImageMap(tileset_path, tilesize)

        self.height = Config().window.height // tilesize[0]
        self.width = Config().window.width // tilesize[1]

        self.grid = [
            [(32, 1, 0, 0) for _ in range(self.width)] for _ in range(self.height)
        ]

        # Internal state for cursor and colors
        self._cursor_x = 0
        self._cursor_y = 0
        self._fg_index = 1
        self._bg_index = 0
        self._outline_index = 0

    # Setter and Getter for Cursor Position
    @property
    def cursor(self):
        return self._cursor_x, self._cursor_y

    @cursor.setter
    def cursor(self, position):
        x, y = position
        if 0 <= x < self.width and 0 <= y < self.height:
            self._cursor_x, self._cursor_y = x, y

    # Setter and Getter for Foreground, Background, and Outline
    @property
    def foreground(self):
        return self._fg_index

    @foreground.setter
    def foreground(self, index):
        self._fg_index = index

    @property
    def background(self):
        return self._bg_index

    @background.setter
    def background(self, index):
        self._bg_index = index

    @property
    def outline(self):
        return self._outline_index

    @outline.setter
    def outline(self, index):
        self._outline_index = index

    def set(self, char):
        """Set a character at the current cursor position using the internal colors"""
        if isinstance(char, str):
            char = ord(char)  # Convert character to its ANSI value
        elif not isinstance(char, int) or not (0 <= char <= 127):
            raise ValueError("Character must be a string or an ANSI code (0-127).")

        x, y = self.cursor
        self.grid[y][x] = (char, self._fg_index, self._bg_index, self._outline_index)
        self._advance_cursor()

    def _get_tileset_path(self, filename):
        """Get the path to the tileset file inside the resources folder."""
        # Use importlib.resources to get the path to the resource file
        with pkg_resources.path(blitspersecond.resources, filename) as path:
            return str(path)

    def _advance_cursor(self):
        """Move the cursor to the next position, wrapping lines if necessary."""
        self._cursor_x += 1
        if self._cursor_x >= self.width:
            self._cursor_x = 0
            self._cursor_y += 1

        if self._cursor_y >= self.height:
            self._cursor_y = self.height - 1
            self._scroll_up()

    def _scroll_up(self):
        """Scroll the console content up by one line."""
        self.grid.pop(0)
        self.grid.append([(32, 1, 0, 0) for _ in range(self.width)])

    def print(self, text, word_wrap=True):
        """Print a string to the console, handling line wrapping, scrolling, and ensuring a newline at the end."""
        text = text.replace("\r\n", "\n").replace("\n\r", "\n").replace("\r", "\n")

        # Ensure the text ends with a newline
        if not text.endswith("\n"):
            text += "\n"

        words = text.split(" ") if word_wrap else list(text)

        for word in words:
            if word_wrap and len(word) + self._cursor_x > self.width:
                self._cursor_x = 0
                self._cursor_y += 1

            for char in word:
                if char == "\n":
                    self._cursor_x = 0
                    self._cursor_y += 1
                    if self._cursor_y >= self.height:
                        self._cursor_y = self.height - 1
                        self._scroll_up()
                else:
                    self.set(char)

            if word_wrap:
                self.set(" ")  # Add a space after each word

    def write(self, text):
        """Write a string to the console from the current cursor position without wrapping or adding a newline."""
        for char in text:
            self.set(char)

    def get(self, x, y):
        """Get the character and color info at position (x, y)"""
        if 0 <= x < self.width and 0 <= y < self.height:
            return self.grid[y][x]
        return None

    def clear(self, char=32, fg_index=1, bg_index=0, outline_index=0):
        """Clear the console with the specified character and colors, and reset the cursor to (0, 0)."""
        self.grid = [
            [(char, fg_index, bg_index, outline_index) for _ in range(self.width)]
            for _ in range(self.height)
        ]
        self._cursor_x = 0
        self._cursor_y = 0

    def __str__(self):
        """Return a string representation of the text console with only characters"""
        output = []
        for row in self.grid:
            output.append("".join(chr(cell[0]) for cell in row))
        return "\n".join(output)
