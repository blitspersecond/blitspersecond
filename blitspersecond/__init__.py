# import pyglet
# from typing import Callable
# from .framebuffer import FrameBuffer
# from .config import Config
# from .console import Console
# from .display import Display
# from .metrics import Metrics
# from .platform import PlatformSupport
# from .imagebank import ImageBank
# from .logger import Logger

import pyglet
from typing import Callable
from .framebuffer import FrameBuffer
from .display import Display


class BlitsPerSecond(object):
    def __init__(self):
        self._eventloop = pyglet.app.EventLoop()
        self._framebuffer = FrameBuffer()
        self._display = Display(self._eventloop, lambda dt: None)

    def _run(self, dt: float):
        try:
            self._callback(self)
            self._display.update(self._framebuffer.texture)
        except KeyboardInterrupt:
            pass

    def run(self, callback: Callable[["BlitsPerSecond"], None]):
        try:
            self._callback = callback
            pyglet.clock.schedule_interval(self._run, 1.0 / 60)
            # self._eventloop.run(1.0 / 60)
            self._eventloop.run(0)
        except KeyboardInterrupt:
            pass

    @property
    def framebuffer(self) -> FrameBuffer:
        return self._framebuffer
