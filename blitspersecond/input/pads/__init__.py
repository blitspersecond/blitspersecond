"""Gamepads: the vocabulary first, the transport and the pad to follow.

    button.py    `Button` -- what a pad has, closed and settled.

The audit this is being rebuilt from is `docs/bps/PADS_PORT.md`.
"""

from .button import Button

__all__ = ["Button"]
