"""The DSP vocabulary: oscillators, envelopes, filters, and effects.

Every one of them is a small kernel over one 400-sample block, controlled
entirely by timestamped register writes. They are assembled into a `Program`
and bound to a `Stage`; none of them knows where in a graph it sits.
"""

from .band_pass import BandPass
from .clip import Clip
from .crush import Crush
from .delay import Delay
from .duck import Duck
from .echo import Echo
from .envelope import Envelope
from .gain import Gain
from .high_pass import HighPass
from .low_pass import LowPass
from .metal import Metal
from .mix import Mix
from .noise import Noise
from .operator import Compositor, Operator
from .pcm import PCM
from .pluck import Pluck
from .pulse import Pulse
from .resonator import Resonator
from .reverb import Reverb
from .ring_mod import RingMod
from .saw import Saw
from .sine import Sine
from .square import Square
from .tanh import Tanh
from .triangle import Triangle

__all__ = (
    "BandPass",
    "Clip",
    "Compositor",
    "Crush",
    "Delay",
    "Duck",
    "Echo",
    "Envelope",
    "Gain",
    "HighPass",
    "LowPass",
    "Metal",
    "Mix",
    "Noise",
    "Operator",
    "PCM",
    "Pluck",
    "Pulse",
    "Resonator",
    "Reverb",
    "RingMod",
    "Saw",
    "Sine",
    "Square",
    "Tanh",
    "Triangle",
)
