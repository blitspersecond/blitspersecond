from typing import Callable
from .render import Renderer

import pyglet


HEIGHT = 360
WIDTH = 640
SCALE = 1


class Display(object):
    def __init__(self, eventloop: pyglet.app.EventLoop, callback: Callable) -> None:
        self._window = pyglet.window.Window(
            WIDTH * SCALE,
            HEIGHT * SCALE,
            vsync=False,
            config=pyglet.gl.Config(swap_interval=1),
        )
        self._callback = callback
        self._eventloop = eventloop

        self._renderer = Renderer()

        @self._window.event
        def on_close():
            pyglet.clock.unschedule(self._callback)
            self._eventloop.exit()
            self._window.close()

    def update(self, texture: pyglet.image.Texture) -> None:
        self._window.clear()
        self._renderer.render(texture)
        self._window.dispatch_events()
        self._window.dispatch_event("on_draw")
