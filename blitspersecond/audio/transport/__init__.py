"""Getting blocks out of the graph and into something: a device, or a file.

**Two transports, and the only difference is what paces them.** `Driver` hands
each block to PortAudio and blocks until the sound card has taken it; `render`
takes blocks as fast as the CPU can make them. Neither mentions a Stage, an
operator or a register -- both call `advance()`, which is the whole of this
package's dependency on synthesis.

**Not user-side, and that is why it is down here.** A game is handed an
`AudioEngine` that is already being driven; it never opens a stream, picks a
device, or starts a thread. An import that says `transport` is admitting it is
a tool rather than a game.

**For the live path, the blocking write is the clock** -- no sleeping, no
timing, no chasing a deadline. That is the reason audio gets a thread at all,
as the single exception to the engine's poll-at-the-cut rule. **For the
offline path there is no clock**, which is what makes a render deterministic
and diffable.

`devices.py` is where per-platform choices live, and Windows deliberately
raises rather than opening the wrong endpoint.

The transports, the handshake and the device policy:
`bps/reference/audio/transport.md`.
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
