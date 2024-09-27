from blitspersecond import BlitsPerSecond


def loop(bps: BlitsPerSecond):
    if not hasattr(loop, "step"):
        loop.step = 0
    if not hasattr(loop, "elapsed_time"):
        loop.elapsed_time = 0.0
    blits = 400
    frames = 1000
    layer = bps.framebuffer[7]
    ansi = bps.imagebank.get("img/ansi.png", (32, 32))
    tile = ansi[0]
    last_deltatime = bps._metrics.last_dt  # last frame delta time
    loop.elapsed_time += last_deltatime
    # Perform blitting 400 times
    for _ in range(blits):
        layer.blit(tile, 0, 0)

    loop.step += 1
    if loop.step >= frames:  # After 1000 frames, print statistics
        pixel_size = 4 * tile.width * tile.height

        # Calculate blits per second (based on delta time and 1000 iterations)
        blits_per_second = frames * blits / loop.elapsed_time

        # Total pixels processed (400 blits * 32x32 pixels)
        total_pixels = pixel_size * blits * frames / loop.elapsed_time

        # Pixel fill rate (pixels per second)
        pixel_fill_rate = total_pixels / loop.elapsed_time

        # Print statistics
        print(f"Sprite Shape: {tile.width}x{tile.height}")
        print(f"Blits per second: {blits_per_second:.2f}")
        print(f"Total pixels processed: {total_pixels}")
        print(f"Pixel fill rate: {pixel_fill_rate:.2f} pixels/second")
        print(f"Delta time (last frame): {bps._metrics.last_dt:.4f} seconds")

        # Optionally exit after printing
        exit(0)


bps = BlitsPerSecond()
bps.run(loop)
