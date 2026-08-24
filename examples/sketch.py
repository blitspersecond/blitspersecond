from blitspersecond import BlitsPerSecond
from blitspersecond.console import Console

from blitspersecond.graphics.glyph import CharSet

bps = BlitsPerSecond()

c = Console(CharSet.ASCII8X12)  # should this self register as a layer? Think about it.
l1 = bps.layers.add(c)


c.print(
    "Hello world", at=(0, 0)
)  # convenience method for console, does new line stuffs - ignore for now
c.write("Hello world")  # advances cursor
c.write("Hello World")

c.write("bah" * 20)

c.layer.cell[1, 10] = ord("A")

c.layer.scroll(0, 1)  # scrolls the layer up one row, leaving a blank row at the bottom
assert ["hello"][
    0
] == "hello"  # just to make sure pytest doesn't complain about no asserts


for i in range(20):
    c.write("boo..")


def loopa(bps: BlitsPerSecond) -> None:
    pass


if __name__ == "__main__":
    bps.run(loopa)
