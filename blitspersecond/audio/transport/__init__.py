"""Getting blocks out of the graph and into something: a device, or a file.

**Two transports, and the only difference is what paces them.** `Driver`
hands each block to PortAudio and blocks until the sound card has taken it;
`render` takes blocks as fast as the CPU can make them and returns an array
or writes a WAV. Neither mentions a Stage, an operator or a register. Both
call `advance()` and deal with what comes back, which is the whole of this
package's dependency on synthesis.

**Not user-side, and that is why it is down here.** A game is handed an
`AudioEngine` that is already being driven; it never opens a stream, picks a
device, or starts a thread. Everything here is plumbing the engine wires up
at boot, or a tool reaching for audio deliberately -- and an import that says
`transport` is an import that admits which of those it is doing.

**For the live path, the blocking write is the clock.** `Driver` does not
sleep, time itself, or chase a deadline. It hands over one block and blocks
until the device has consumed it, which is the one back-pressure signal that
comes from the sound card rather than from a guess about it -- and the reason
audio gets a thread at all, as the single exception to the engine's
poll-at-the-cut rule.

**For the offline path there is no clock**, and that is the point of it: a
render is deterministic, diffable against the last one, and faster than
realtime. It is how a change to an operator is compared with what it used to
sound like.

`devices.py` is where per-platform choices live. Linux and macOS take
PortAudio's default; Windows raises rather than silently landing on MME,
because a wrong-but-working device is a bug that reaches a player as bad
latency and never as an error.
"""

from .devices import default_output_device
from .driver import Driver, play
from .offline import render, render_wav, write_wav

__all__ = (
    "default_output_device",
    "Driver",
    "play",
    "render",
    "render_wav",
    "write_wav",
)
