from blitspersecond import BlitsPerSecond


def loop(bps: BlitsPerSecond):
    if not hasattr(loop, "step"):
        loop.step = 0
    if not hasattr(loop, "elapsed_time"):
        loop.elapsed_time = 0.0
    frames = 100
    layer = bps.framebuffer[7]
    layer.palette[2] = (255, 255, 255, 255)
    ansi = bps.imagebank.get("blitspersecond/resources/ascii12x8.png", (8, 12))
    blits = 0
    for _ in range(frames):
        last_deltatime = bps._metrics.last_dt  # last frame delta time
        loop.elapsed_time += last_deltatime
        x = 0
        y = 0
        while y < 24:
            for tile in ansi:
                print(tile.width, tile.height)
                layer.blit(tile, x, y)
                blits += 1
                x += tile.width
                if x >= 640:
                    x = 0
                    y += tile.height

            print(tile.height, tile.width)
            # exit(0)

    pixel_size = 4 * ansi[0].height * ansi[0].width

    # Calculate blits per second (based on delta time and 1000 iterations)
    blits_per_second = frames * blits / loop.elapsed_time

    # Total pixels processed (400 blits * 32x32 pixels)
    total_pixels = pixel_size * blits * frames / loop.elapsed_time

    # Pixel fill rate (pixels per second)
    pixel_fill_rate = total_pixels / loop.elapsed_time

    # Print statistics
    print(f"Sprite Shape: {ansi[0].width}x{ansi[0].height}")
    print(f"Blits per second: {blits_per_second:.2f}")
    print(f"Total pixels processed: {total_pixels}")
    print(f"Pixel fill rate: {pixel_fill_rate:.2f} pixels/second")
    print(f"Delta time (last frame): {bps._metrics.last_dt:.4f} seconds")

    # Optionally exit after printing
    exit(0)


bps = BlitsPerSecond()
bps.run(loop)
