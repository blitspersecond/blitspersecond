"""The MixingDesk: where the graph stops being mono and becomes a picture.

Everything upstream of here is one channel. A desk gives each connected Stage
a complete strip -- level, then stereo placement -- and sums the strips. That
is the only place in the engine where two channels exist, which is what keeps
every Operator, Bus and delay line mono and half the size.

    connected Stage -> copy -> Gain -> constant-power Pan -> stereo Mix

A desk's output cannot feed an ordinary Stage. A genuinely stereo effect
would need a stereo graph contract of its own rather than every DSP path
quietly doubling.
"""

from typing import cast

from .bus import StereoBus
from .connection import _Connection
from .operators.pan import _Pan
from .stage import Stage
from .stages import _InputStage


class _DeskConnection(_Connection):
    """One strip: an ordinary lane, plus placement and its own stereo block."""

    def __init__(self, stage: Stage, source: Stage):
        super().__init__(stage, source)
        self.pan = _Pan()
        # Lane-scoped like the strip's level, so `pan` is addressed with the
        # connected Stage rather than being one value for the whole desk.
        self._declare_registers(self.pan.registers)
        #: Held across blocks. A strip is the only thing in the engine that
        #: needs two channels, and there are a handful of them, so these are
        #: owned outright rather than rented from the mono pool.
        self.stereo = StereoBus()

    def process_stereo(self) -> StereoBus:
        """Run the lane as usual, then place the result between the speakers."""
        mono = super().process()
        self.control(self.pan)
        output = self.pan.route(mono, self.stereo)
        self._quiescent = output.current.quiescent
        return output


class MixingDesk(_InputStage[_DeskConnection]):
    """The terminal Stage: mono strips in, one stereo block out.

    Has no Program. A desk is routing rather than processing -- anything that
    wants to change the sound is a Stage upstream of it -- so there is nothing
    here for a Program to be attached to, and no lane-shadowing question of
    the kind a Composite has.

    An empty desk settles stereo silence, which is what lets a Driver start
    and hold the device open before a game has built any graph at all.
    """

    channels = 2

    def _make_output(self) -> StereoBus:
        return StereoBus()

    def _make_connection(self, source: Stage) -> _DeskConnection:
        return _DeskConnection(self, source)

    def _render(self) -> StereoBus:
        inputs = []
        for connection in self._connections:
            if connection.quiescent:
                continue
            output = connection.process_stereo()
            if not output.current.quiescent:
                inputs.append(output)
        # Mix is shape-polymorphic and typed on the mono Bus; see the note
        # in its constructor. This is the stereo half of that one fact.
        return cast(StereoBus, self._compositor.process(*inputs))
