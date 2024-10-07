from blitspersecond.image import Image
from blitspersecond.resourcemanager import ResourceManager
from blitspersecond.tileset import TileSet
from blitspersecond.layer import Layer
from blitspersecond.framebuffer import FrameBuffer
import timeit

im = Image("blitspersecond/resources/ascii12x8.png")

rm = ResourceManager()
im = rm.get_image("blitspersecond/resources/ascii12x8.png")
pal = im.palette
ts = TileSet(im, (32, 32))
print(ord("A"))
# letter_A = ts[ord("A")]
letter_A = ts[8]


for i in range(10):
    pal[0] = (0, 0, 0, i * 16)
    print(pal.version)

fb = FrameBuffer()
layer = fb.layer(7)
layer.blit(letter_A, 0, 0)
time = timeit.timeit("layer.blit(letter_A, 0, 0)", globals=globals(), number=1000000)
blitspersecond = 1000000 / time

print(f"{blitspersecond:.2f} blits per second")
print(f"BlitsPerFrame: {blitspersecond / 60:.2f}")

time = timeit.timeit(
    "layer.old_blit(letter_A, 0, 0)", globals=globals(), number=1000000
)
blitspersecond = 1000000 / time

print(f"{blitspersecond:.2f} blits per second")
print(f"BlitsPerFrame: {blitspersecond / 60:.2f}")
