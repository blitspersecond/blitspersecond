from typing import Callable
from .config import Config
import pyglet
from pyglet.gl import (
    glBindTexture,
    glTexParameteri,
    GL_TEXTURE_2D,
    GL_TEXTURE_MIN_FILTER,
    GL_NEAREST,
    GL_TEXTURE_MAG_FILTER,
)


class Display:
    def __init__(self, eventloop: pyglet.app.EventLoop, callback: Callable) -> None:
        self._window = pyglet.window.Window(
            Config().window.width * Config().window.scale,  # 640 * 3
            Config().window.height * Config().window.scale,  # 360 * 3
            vsync=Config().window.vsync,  # 60
        )
        self._callback = callback
        self._eventloop = eventloop

        @self._window.event
        def on_close():
            pyglet.clock.unschedule(self._callback)
            self._eventloop.exit()
            self._window.close()

    def update(self, texture: pyglet.image.Texture):
        self._window.clear()
        _t = texture
        glBindTexture(GL_TEXTURE_2D, _t.id)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST)

        _t.height = Config().window.height * Config().window.scale
        _t.width = Config().window.width * Config().window.scale
        _t.blit(0, 0)
        self._window.dispatch_events()
        self._window.dispatch_event("on_draw")
        try:
            self._window.flip()
        except AttributeError:
            print("window has been closed")
