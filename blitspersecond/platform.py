import platform
import ctypes


class PlatformSupport:
    """
    PlatformSupport is a Singleton class that ensures pixels are rendered one-to-one
    on different operating systems. It also provides a hook for other platform-specific
    settings. These settings are applied globally at the platform level to ensure
    consistent pixel rendering, especially for high-DPI displays.

    This class is intended to be instantiated once, ensuring that platform-specific
    settings are configured as needed without unnecessary repetition.
    """

    _instance = None

    def __new__(cls):
        """Ensure that only one instance of PlatformSupport is created (Singleton)."""
        if cls._instance is None:
            cls._instance = super(PlatformSupport, cls).__new__(cls)
            cls._instance.configure_pixel_scaling()
        return cls._instance

    @staticmethod
    def configure_pixel_scaling():
        """
        Configures pixel scaling for different platforms to ensure 1:1 pixel rendering.

        Platform-specific behaviors:
        - Windows: Adjusts DPI awareness for accurate pixel scaling.
        - macOS: May manage Retina display scaling (automatic in most cases).
        - Linux: Desktop environment settings are expected to handle scaling.

        This method can be extended to hook in other platform-specific settings as needed.
        """
        current_platform = platform.system()

        if current_platform == "Windows":
            PlatformSupport._set_windows_pixel_scaling()
        elif current_platform == "Darwin":  # macOS (OSX)
            PlatformSupport._set_macos_pixel_scaling()
        elif current_platform == "Linux":
            PlatformSupport._set_linux_pixel_scaling()
        else:
            print(
                f"Platform {current_platform} is not explicitly supported for pixel scaling."
            )

    @staticmethod
    def _set_windows_pixel_scaling():
        """
        Configures Windows to ensure 1:1 pixel rendering by adjusting DPI awareness.
        This method accounts for differences between modern and older versions of Windows.
        """
        try:
            if hasattr(ctypes.windll.shcore, "SetProcessDpiAwareness"):
                # Windows 8.1 or later
                ctypes.windll.shcore.SetProcessDpiAwareness(
                    2
                )  # Per-monitor DPI awareness
                print("Pixel scaling set for Windows (8.1 or later).")
            else:
                # Fallback for older Windows versions
                ctypes.windll.user32.SetProcessDPIAware()  # System DPI awareness
                print("Pixel scaling set for older Windows versions.")
        except AttributeError:
            print("Failed to set DPI awareness. Unsupported Windows version.")
        except Exception as e:
            print(f"Error while setting Windows pixel scaling: {e}")

    @staticmethod
    def _set_macos_pixel_scaling():
        """
        Configures macOS to ensure 1:1 pixel rendering.
        macOS handles DPI scaling automatically, particularly on Retina displays.
        Additional platform-specific settings can be added here if needed.
        """
        print("macOS pixel scaling is typically automatic (Retina displays).")

    @staticmethod
    def _set_linux_pixel_scaling():
        """
        Ensures Linux renders pixels 1:1, depending on desktop environment settings.
        Typically, the desktop environment (e.g., GNOME, KDE) manages pixel scaling.
        """
        print(
            "Linux pixel scaling is typically managed by the desktop environment (e.g., GNOME, KDE)."
        )


# Usage Example (Singleton ensures only one instance is created):
# platform_support = PlatformSupport()
