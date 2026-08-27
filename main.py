"""Smallest runnable BPS program: open a window and hold 60 ticks a second.

The graphics rebuild has the layer compose pass out, so there is nothing to
draw yet. What this proves is the machine: the engine clock holding the
authored rate, display presenting on its own deadline grid, and a game tick
whose effect reaches the panel -- here as the background colour, which is
what stands in for compose until layers return.
"""

from math import sin, tau

from blitspersecond import BlitsPerSecond, GameLoop


class Demo(GameLoop):
    def run(self) -> None:
        # One cycle every two seconds, counted in FRAMES, not wall seconds --
        # which is the engine's whole position on what time is.
        phase = (self.bps.clock.frame % 120) / 120
        level = 0.5 + 0.5 * sin(phase * tau)
        self.bps.display.background = (level * 0.2, level * 0.1, level * 0.4)


if __name__ == "__main__":
    bps = BlitsPerSecond()
    bps.start(Demo)
