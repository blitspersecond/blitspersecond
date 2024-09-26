from blitspersecond import BlitsPerSecond
import blitspersecond
import pyglet

import blitspersecond.compositor

manager = pyglet.input.ControllerManager()

# devices = pyglet.input.get_devices()
# for device in devices:
#     print(device.get_guid(), device.manufacturer, device.name)

controllers = pyglet.input.get_controllers()
for controller in controllers:
    print(
        controller.device.get_guid(),
        controller.device.manufacturer,
        controller.device.name,
    )
    controller.rumble_play_weak(1, 0.5)


@manager.event
def on_connect(controller):
    print(f"Connected:  {controller}")


@manager.event
def on_disconnect(controller):
    print(f"Disconnected:  {controller}")


def gameloop():
    pass


def main():
    # bps = BlitsPerSecond(gameloop)
    l = blitspersecond.compositor.Compositor(4, 8)
    for i in l:
        pass
        # print(i[:, 0])
    print(l[7][:, 0])
    l.clear()
    print(l[7][:, 0])

    p = blitspersecond.compositor.Palette()


if __name__ == "__main__":
    # main()
    # pic = pyglet.image.load("img/ansi.png")
    # width, height = pic.width, pic.height
    # print(width, height)
    # print(pic.format, pic.pitch)
    # print(dir(pic))
    # image_data = pic.get_image_data()
    # print(image_data)
    # data = image_data.get_data("RGBA", width * 4)
    # print(data)

    from PIL import Image
    import numpy as np

    # Load the palettized PNG image
    image = Image.open("img/ansi.png")

    # Ensure the image is in P mode (palettized)
    if image.mode != "P":
        raise ValueError("The image is not in palettized (P mode) format.")

    # Get the palette as an array
    palette = np.array(image.getpalette()).reshape(-1, 3)  # Reshape into (N, 3) for RGB

    # Get the pixel data as an array of indices (referencing the palette)
    pixel_data = np.array(image)

    print("Palette:\n", palette)
    print("Pixel Data (indices):\n", pixel_data)
