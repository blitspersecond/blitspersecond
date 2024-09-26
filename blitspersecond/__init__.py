import pyglet
from .framebuffer import FrameBuffer
from .config import Config
from .display import Display
from .metrics import Metrics
from .platform import PlatformSupport
from .imagebank import ImageBank


class BlitsPerSecond:
    def __init__(self):
        PlatformSupport()
        self._eventloop = pyglet.app.EventLoop()

        self._metrics = Metrics(target_fps=Config().window.framerate)
        self._framebuffer = FrameBuffer()
        self._display = Display(self._eventloop, lambda dt: None)
        self._graphicsdata = ImageBank()

    def run(self, callback: callable):
        self._callback = callback
        pyglet.clock.schedule_interval(
            self._run, 1.0 / 144
        )  # Config().window.framerate)
        self._eventloop.run(144)

    def _run(self, dt: float):
        self._metrics(dt)
        self._callback(self)
        self._display.update(self._framebuffer.texture)

        if Config().default.show_fps == True:
            print(
                f"FPS: {self._metrics.last_fps:.2f} | 99th percentile: {self._metrics.percentile_99:.2f}"
            )

    @property
    def framebuffer(self):
        return self._framebuffer

    @property
    def images(self):
        return self._framebuffer.texture
