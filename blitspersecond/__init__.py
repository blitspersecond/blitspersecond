import pyglet
from typing import Callable
from .framebuffer import FrameBuffer
from .config import Config
from .display import Display
from .metrics import Metrics
from .platform import PlatformSupport
from .imagebank import ImageBank
from .logger import Logger


class BlitsPerSecond(object):
    def __init__(self):
        PlatformSupport()
        self._eventloop = pyglet.app.EventLoop()
        self._metrics = Metrics()
        self._framebuffer = FrameBuffer()
        self._display = Display(self._eventloop, lambda dt: None)
        self._imagebank = ImageBank()

    def run(self, callback: Callable[["BlitsPerSecond"], None]):
        self._callback = callback
        pyglet.clock.schedule_interval(self._run, 1.0 / Config().window.framerate)
        if Config().window.vsync:
            self._eventloop.run(Config().window.framerate)
        else:
            self._eventloop.run(None)

    def _run(self, dt: float):
        self._metrics(dt)
        self._callback(self)
        self._display.update(self._framebuffer.texture)

        if Config().default.show_fps == True:
            Logger().debug(
                f"FPS: {self._metrics.last_fps:.2f} | 99th percentile: {self._metrics.percentile_99:.2f}"
            )

    @property
    def framebuffer(self) -> FrameBuffer:
        return self._framebuffer

    @property
    def imagebank(self) -> ImageBank:
        return self._imagebank
