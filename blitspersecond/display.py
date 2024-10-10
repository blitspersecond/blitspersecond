from typing import Callable
from .config import Config
from .render import Renderer
from .keyboard import Keyboard
from .platform import Platform
import pyglet
from pyglet.window import key


class Display(object):
    def __init__(self, eventloop: pyglet.app.EventLoop, callback: Callable) -> None:
        c = Config()
        self._platform = Platform()
        self._window = pyglet.window.Window(
            c.window.width * c.window.scale * c.window.dpi_scale,
            c.window.height * c.window.scale * c.window.dpi_scale,
            vsync=False,
            config=pyglet.gl.Config(swap_interval=1),
        )
        self._callback = callback
        self._eventloop = eventloop

        self._keyboard = Keyboard()

        # self._keyboard.mode = "text"

        def hello_world_action():
            print("hello world")

        self._keyboard.add_key_combination(
            {key.MOD_CTRL, key.MOD_COMMAND}, key.H, hello_world_action
        )

        self._renderer = Renderer()

        @self._window.event
        def on_close():
            pyglet.clock.unschedule(self._callback)
            self._eventloop.exit()
            self._window.close()

        @self._window.event
        def on_key_press(symbol, modifiers):
            # Delegate to the Keyboard handler
            self._keyboard.on_key_press(symbol, modifiers)
            # Handle window resizing based on key combinations
            if (modifiers & key.MOD_COMMAND) and symbol == key.MINUS:
                self._resize_window(2)
            elif (modifiers & key.MOD_COMMAND) and symbol == key.EQUAL:
                self._resize_window(3)

        @self._window.event
        def on_text(text):
            self._keyboard.on_text(text)

        @self._window.event
        def on_key_release(symbol, modifiers):
            # Delegate to the Keyboard handler
            self._keyboard.on_key_release(symbol, modifiers)

        @self._window.event
        def on_key_release(symbol, modifiers):
            # Delegate to the Keyboard handler
            self._keyboard.on_key_release(symbol, modifiers)

    def update(self, texture: pyglet.image.Texture) -> None:
        self._window.clear()
        self._renderer.render(texture)
        if self._keyboard.is_key_held(key.LSHIFT):
            print("LSHIFT is being held down!")

        if self._keyboard.is_combination_held({key.MOD_CTRL}, key.A):
            print("CTRL + A is being held down!")
        self._window.dispatch_events()
        self._window.dispatch_event("on_draw")

    def _resize_window(self, scale):
        """
        Resize the window to specified dimensions.
        """
        c = Config()
        # self._window = pyglet.window.Window(
        #     c.window.width * c.window.scale * c.window.dpi_scale,
        #     c.window.height * c.window.scale * c.window.dpi_scale,
        c.window.scale = scale

        self._window.set_size(
            c.window.width * c.window.scale * c.window.dpi_scale,
            c.window.height * c.window.scale * c.window.dpi_scale,
        )

        c.save()
