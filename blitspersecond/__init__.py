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
from .metrics import Metrics
from .display import Display


class BlitsPerSecond(object):
    def __init__(self):
        self._eventloop = pyglet.app.EventLoop()
        self._framebuffer = FrameBuffer()
        self._display = Display(self._eventloop, lambda dt: None)
        self._metrics = Metrics()

    def _run(self, dt: float):
        self._metrics(dt)
        self._callback(self)
        self._display.update(self._framebuffer.texture)
        print(
            f"FPS: {self._metrics.last_fps:.2f} | 99th percentile: {self._metrics.percentile_99:.2f} - DT: {dt:.2f}"
        )
        # if Config().default.show_fps == True:
        #     pass
        # Logger().debug(
        #     f"FPS: {self._metrics.last_fps:.2f} | 99th percentile: {self._metrics.percentile_99:.2f} - DT: {dt:.2f}"
        # )

    def run(self, callback: Callable[["BlitsPerSecond"], None]):
        try:
            self._callback = callback
            pyglet.clock.schedule_interval(self._run, 1.0 / 60)
            # self._eventloop.run(1.0 / 60)
            self._eventloop.run(0)  # 0 TODO: for Mac, None for PC?
        except KeyboardInterrupt:
            pass

    @property
    def framebuffer(self) -> FrameBuffer:
        return self._framebuffer
