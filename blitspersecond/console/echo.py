"""Console presentation for a keyboard-owned stream-backed Input."""

from __future__ import annotations

from blitspersecond.colors import Pen
from blitspersecond.input.kbm.input import Input

from .console import Console


class _ConsoleEcho:
    """Observe one Input and echo its replayed value at a Console cursor."""

    def __init__(self, console: Console, entry: Input) -> None:
        self._console = console
        self._input_pen = Pen(*console.pen)
        self._drawn = ""
        self._end = (console.cursor.x, console.cursor.y)
        entry._observe(self)

    def _started(self, entry: Input) -> None:
        self._end = (self._console.cursor.x, self._console.cursor.y)
        self._sync(entry)

    def _changed(self, entry: Input) -> None:
        self._sync(entry)

    def _finished(self, entry: Input) -> None:
        self._sync(entry)
        console = self._console
        console.cursor = self._end
        console._newline()

    def _discarded(self, entry: Input) -> None:
        pass

    def _sync(self, entry: Input) -> None:
        console = self._console
        line = entry.value
        insert = entry.insert
        console.cursor = self._end

        common = 0
        for old, new in zip(self._drawn, line):
            if old != new:
                break
            common += 1

        with console._using_pen(self._input_pen):
            for _ in self._drawn[common:]:
                self._back()
                console.glyphs.cell[console.cursor.x, console.cursor.y] = 0

            for character in line[common:]:
                code = ord(character)
                console.glyphs.cell[console.cursor.x, console.cursor.y] = (
                    code if code <= 0xFF else ord("?")
                )
                x = console.cursor.x + 1
                if x >= console.cols:
                    console._newline()
                else:
                    console.cursor = (x, console.cursor.y)

        self._drawn = line
        self._end = (console.cursor.x, console.cursor.y)
        for _ in range(len(line) - insert):
            self._back()

    def _back(self) -> None:
        console = self._console
        if console.cursor.x:
            console.cursor = (console.cursor.x - 1, console.cursor.y)
        elif console.cursor.y:
            console.cursor = (console.cols - 1, console.cursor.y - 1)
