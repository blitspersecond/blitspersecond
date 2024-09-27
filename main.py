from blitspersecond import BlitsPerSecond


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


def loop(bps: BlitsPerSecond):
    ansi = bps.imagebank.get("blitspersecond/resources/ascii12x8.png", (8, 12))
    if not hasattr(loop, "step"):
        loop.step = 0
    x = 0
    y = 180
    layer = bps.framebuffer[7]
    for tile in ansi:
        layer.palette[2] = color_transition(loop.step)
        x += tile.width
        if x >= layer.width:
            x = 0
            y += tile.height
            if y > layer.height:
                y = 0
        layer.blit(tile, x, y)
    loop.step += 1


app = BlitsPerSecond()
app.run(loop)
