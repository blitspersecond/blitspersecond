"""Which output device the engine opens, per platform.

sounddevice is the transport everywhere, but "the default output" means
different things on different systems, and one of them is a trap.
"""

import platform


def default_output_device(sounddevice, system: str | None = None):
    """The device selector to hand `sounddevice.OutputStream`.

    **Linux and macOS: PortAudio's own default**, returned as `None`. On the
    tested Linux path that reaches the shared PipeWire sink through PortAudio's
    ALSA view, which is the right answer -- it is the same sink everything
    else on the desktop mixes into, so the engine does not seize the card or
    bypass the user's volume.

    **Windows: deliberately unimplemented, and loud about it.** PortAudio's
    overall default there can be a legacy MME view even when the useful
    endpoint is available through WASAPI, so falling through to `None` would
    silently pick the worse device and sound like a mystery. Selecting WASAPI's
    current default needs a topology query written against real hardware, and
    that is its own pass; see the board. Raising is the honest interim.
    """
    current_system = platform.system() if system is None else system
    if current_system == "Windows":
        raise NotImplementedError(
            "Windows output selection is not built yet. PortAudio's default "
            "here may be a legacy MME endpoint, so the engine refuses rather "
            "than opening the wrong device; WASAPI selection is a pending pass."
        )
    return None
