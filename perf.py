from blitspersecond import BlitsPerSecond
from blitspersecond.resourcemanager import ResourceManager
from blitspersecond.tileset import TileSet

from numpy import array

from numba import njit, prange

from datetime import timedelta

import cProfile
import pstats

profiler = cProfile.Profile()


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


rm = ResourceManager()
ansi = rm.get_image("blitspersecond/resources/ascii12x8.png")
tileset = TileSet(ansi, (8, 12))

# lets add a timer that measures the time in seconds and milliseconds here

step = 0
blits = 0

import time


start_time = time.perf_counter()


def loop(bps: BlitsPerSecond):
    global step, blits, start_time
    x, y = 0, 0

    # Set the color for the tile
    for layer in bps.framebuffer:
        layer.palette[2] = color_transition(step)

    tile = tileset[ord("X")]

    # Blit tiles across the layer
    for layer in bps.framebuffer:
        for x in range(0, 80):
            for y in range(0, 30):
                layer.blit(tile, x * 8, y * 12)
                blits += 1

    step += 1

    if step == 1:
        profiler.enable()  # Start profiling after initial setup

    # Every 100 steps, calculate blits per second and reset
    if step % 100 == 0:
        # Calculate time elapsed
        duration = timedelta(seconds=time.perf_counter() - start_time)

        # Calculate blits per second
        blits_per_second = (
            blits / duration.total_seconds() if duration.total_seconds() > 0 else 0
        )
        print(f"Step: {step}, Blits Per Second: {blits_per_second:.2f}")

        # Reset for the next interval
        blits = 0
        start_time = time.perf_counter()


def main():
    # profiler.enable()
    app = BlitsPerSecond()
    app.run(loop)
    profiler.disable()
    # stats = pstats.Stats(profiler)
    # stats.strip_dirs().sort_stats("cumtime").print_stats(25)


if __name__ == "__main__":
    main()
