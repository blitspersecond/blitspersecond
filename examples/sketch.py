from blitspersecond import BlitsPerSecond
from blitspersecond.console import Console

from blitspersecond.graphics.glyph import CharSet
from blitspersecond.resources.tmx import TiledFile

bps = BlitsPerSecond()


from blitspersecond.resources import ResourceManager

# tmx = ResourceManager().tmx("assets/gorillaz/island.tmx")
fg_tmx = ResourceManager().tmx("../experiments/assets/tiled/sonic/Sonic_md_fg1.tmx")
fg_tm = fg_tmx.tilemap("Layer 1")

bg_tmx = ResourceManager().tmx("../experiments/assets/tiled/sonic/Sonic_md_bg1.tmx")
bg_tm = bg_tmx.tilemap("Layer 1")

fg = bps.layers.add(fg_tm)
bg = bps.layers.add(bg_tm)

console = bps.layers.add(Console(CharSet.ASCII8X12))

console.write("Let the Bat be Latina", at=(10, 10))

bg.before(fg)

x = 0


def main(bps: BlitsPerSecond):
    global x
    bg.scroll = (x // 2, 0)
    fg.scroll = (x, 0)
    x += 2

    pass


bps.run(main)
