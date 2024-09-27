from blitspersecond import BlitsPerSecond


def loop(bps: BlitsPerSecond):
    if not hasattr(loop, "step"):
        loop.step = 0
    ansi = bps.imagebank.get("img/ansi.png", (8, 12))
    layer = bps.framebuffer[7]
    tile = ansi[0]
    # put testing code here
    loop.step += 1
    exit(0)


ansi = ib.get("img/ansi.png", (32, 32))  # 32x32 tiles
frames = 1000
elapsed_time = 0.0
blits = 400
step = 0
elapsed_time = 0.0


bps = BlitsPerSecond()
bps.run(loop)
