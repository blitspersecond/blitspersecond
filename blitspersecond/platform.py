import platform as pt
import ctypes
import pyglet
from Cocoa import NSScreen  # For macOS
from pyglet.window import key
from .config import Config


class Platform:
    def __init__(self):
        self._system = pt.system()
        _display = pyglet.canvas.get_display()
        _screen = _display.get_default_screen()
        _w = _screen.width
        _h = _screen.height

        if self._system == "Darwin":
            # macOS scaling
            main_screen = NSScreen.mainScreen()
            if main_screen:
                scaling_factor = main_screen.backingScaleFactor()
                _w = int(_w * scaling_factor)
                _h = int(_h * scaling_factor)
                Config().window.dpi_scale = 1 / scaling_factor
            else:
                print("Could not access main screen for scaling factor on macOS.")

        elif self._system == "Windows":
            # Windows DPI awareness
            try:
                if hasattr(ctypes.windll.shcore, "SetProcessDpiAwareness"):
                    ctypes.windll.shcore.SetProcessDpiAwareness(
                        2
                    )  # Per-monitor DPI awareness
                    print("Pixel scaling set for Windows 8.1 or later.")
                else:
                    # Fallback for older Windows versions
                    ctypes.windll.user32.SetProcessDPIAware()  # System DPI awareness
                    print("Pixel scaling set for older Windows versions.")
            except AttributeError:
                print("Failed to set DPI awareness. Unsupported Windows version.")
            except Exception as e:
                print(f"Error while setting Windows pixel scaling: {e}")
        Config().save()

        # Store the calculated width and height
        self._width = _w
        self._height = _h
        print(f"Screen dimensions: {self._width}x{self._height}")

        # Initialize key bindings or other platform-specific settings
        self._initialize_key_bindings()

    def set_window_scaling(self, window):
        print(window.__class__())
        window.content_scale = 2

    @property
    def width(self):
        return self._width

    @property
    def height(self):
        return self._height

    def _initialize_key_bindings(self):
        if self._system == "Darwin":
            # Example of macOS-specific key bindings
            self.fullscreen_key = key.MOD_COMMAND | key.F
            print("Using Command + F for fullscreen on macOS.")
        elif self._system == "Windows":
            # Example of Windows-specific key bindings
            self.fullscreen_key = key.MOD_ALT | key.ENTER
            print("Using Alt + Enter for fullscreen on Windows.")
        else:
            # Default or cross-platform bindings
            self.fullscreen_key = key.F11
            print("Using F11 for fullscreen on other platforms.")

    def get_scaling_factor(self):
        if self._system == "Darwin":
            main_screen = NSScreen.mainScreen()
            if main_screen:
                return main_screen.backingScaleFactor()
        return 1  # Default for non-macOS or if scaling factor is unavailable


# Example usage
platform = Platform()
print(f"Screen Width: {platform.width}, Screen Height: {platform.height}")
print(f"Scaling Factor: {platform.get_scaling_factor()}")
