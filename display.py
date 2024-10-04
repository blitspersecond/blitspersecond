import pyglet

display = pyglet.canvas.get_display()
screens = display.get_screens()
windows = []
for screen in screens:
    print(screen.height, screen.width)

# 832 1280
# Macbook Air OSX, actually has a 2560x1664 - any way to get access to pixel scaling data?

import pyglet
from AppKit import NSScreen

# Get pyglet display and screens
display = pyglet.canvas.get_display()
screens = display.get_screens()

# List screen information
for i, screen in enumerate(screens):
    print(f"Screen {i}: Resolution: {screen.width}x{screen.height}")

# Get scaling factor using PyObjC
for i, screen in enumerate(NSScreen.screens()):
    scale_factor = screen.backingScaleFactor()  # Retrieve the scaling factor
    print(f"Screen {i}: Scale Factor: {scale_factor}")


def get_multiples_of_base_resolution(
    screen_width, screen_height, base_width=640, base_height=360
):
    """
    Returns a list of resolutions that are integer multiples of the base resolution
    (640x360 by default) and fit within the given screen resolution.

    Parameters:
        screen_width (int): The width of the given screen resolution.
        screen_height (int): The height of the given screen resolution.
        base_width (int): The width of the base resolution (default is 640).
        base_height (int): The height of the base resolution (default is 360).

    Returns:
        list of tuples: List of resolutions (width, height) that are integer multiples
                        of the base resolution and fit within the given screen resolution.
    """
    resolutions = []
    multiplier = 1

    while True:
        new_width = base_width * multiplier
        new_height = base_height * multiplier

        if new_width > screen_width or new_height > screen_height:
            break

        resolutions.append((new_width, new_height))
        multiplier += 1

    return resolutions


# Example usage:
screen_width = 1280
screen_height = 832
resolutions = get_multiples_of_base_resolution(screen_width, screen_height)
print(resolutions)
