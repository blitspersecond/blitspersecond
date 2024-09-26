def color_transition(step):
    """
    Returns an RGBA color tuple that cycles between red, green, and blue.

    :param step: An integer from 0 to 599 representing the position in the transition.
    :return: A tuple of (R, G, B, A) where R, G, B are integers from 0 to 255 and A is always 255.
    """
    # if not (0 <= step < 600):
    #     raise ValueError("Step must be between 0 and 599")

    # Determine the total steps for each transition (200 steps for each transition)
    step_mod = step % 600

    if step_mod < 200:
        # Transition from red to green
        r = 255 - int((step_mod / 200) * 255)  # Decrease red
        g = int((step_mod / 200) * 255)  # Increase green
        b = 0
    elif step_mod < 400:
        # Transition from green to blue
        g = 255 - int(((step_mod - 200) / 200) * 255)  # Decrease green
        r = 0
        b = int(((step_mod - 200) / 200) * 255)  # Increase blue
    else:
        # Transition from blue to red
        b = 255 - int(((step_mod - 400) / 200) * 255)  # Decrease blue
        r = int(((step_mod - 400) / 200) * 255)  # Increase red
        g = 0

    return (r, g, b, 255)


from blitspersecond import BlitsPerSecond
from blitspersecond.imagebank import ImageBank


app = BlitsPerSecond()

ib = ImageBank()
# ansi = ib.get("img/ansi.png", (32, 32))
# ansi_at = ansi[64]  # Tile 64 is the '@' symbol
layer = app.framebuffer[7]
layer.palette[0] = (0, 0, 0, 0)

step = 0


def loop(bps: BlitsPerSecond):
    global step
    x = 0
    y = 180
    layer = bps.framebuffer[7]
    for tile in ansi:
        # layer.palette[2] = color_transition(step)
        x += tile.width
        if x >= layer.width:
            x = 0
            y += tile.height
            if y > layer.height:
                y = 0
        layer.blit(tile, x, y)
    step += 1


def test_loop(bps: BlitsPerSecond):
    global step
    layer = bps.framebuffer[7]
    tile = ansi[0]
    print("first call, never blitted")
    layer.blit(tile, 0, 0)
    print("second call")
    layer.blit(tile, 0, 0)
    print("third call")
    layer.blit(tile, 0, 0)
    layer = bps.framebuffer[6]
    print("fourth call, layer switch")
    layer.blit(tile, 0, 0)
    exit(0)
    step += 1


ansi = ib.get("img/ansi.png", (32, 32))  # 32x32 tiles
frames = 1000
elapsed_time = 0.0
blits = 400
step = 0
elapsed_time = 0.0


def speed_loop(bps: BlitsPerSecond):
    global step, elapsed_time
    layer = bps.framebuffer[7]
    tile = ansi[0]
    last_deltatime = bps._metrics.last_dt  # last frame delta time
    elapsed_time += last_deltatime
    # Perform blitting 400 times
    for _ in range(blits):
        layer.blit(tile, 0, 0)

    step += 1
    if step >= frames:  # After 1000 frames, print statistics
        pixel_size = 4 * tile.width * tile.height

        # Calculate blits per second (based on delta time and 1000 iterations)
        blits_per_second = frames * blits / elapsed_time

        # Total pixels processed (400 blits * 32x32 pixels)
        total_pixels = pixel_size * blits * frames / elapsed_time

        # Pixel fill rate (pixels per second)
        pixel_fill_rate = total_pixels / elapsed_time

        # Print statistics
        print(f"Sprite Shape: {tile.width}x{tile.height}")
        print(f"Blits per second: {blits_per_second:.2f}")
        print(f"Total pixels processed: {total_pixels}")
        print(f"Pixel fill rate: {pixel_fill_rate:.2f} pixels/second")
        print(f"Delta time (last frame): {bps._metrics.last_dt:.4f} seconds")

        # Optionally exit after printing
        exit(0)


app.run(speed_loop)

# PS C:\Users\kris\Documents\expansive> python .\main.py
# Pixel scaling set for Windows (8.1 or later).
# Sprite Shape: 32x32
# Blits per second: 27175.73
# Total pixels processed: 111311805.41522822
# Pixel fill rate: 7562449.97 pixels/second
# Delta time (last frame): 0.0149 seconds
# PS C:\Users\kris\Documents\expansive> python .\main.py
# Pixel scaling set for Windows (8.1 or later).
# Sprite Shape: 12x8
# Blits per second: 36625.24
# Total pixels processed: 14064091.180586794
# Pixel fill rate: 1287751.70 pixels/second
# Delta time (last frame): 0.0106 seconds
# PS C:\Users\kris\Documents\expansive> python .\main.py
# Pixel scaling set for Windows (8.1 or later).
# Sprite Shape: 64x64
# Blits per second: 13949.77
# Total pixels processed: 228552982.99420527
# Pixel fill rate: 7970652.17 pixels/second
# Delta time (last frame): 0.0291 seconds
# PS C:\Users\kris\Documents\expansive>
