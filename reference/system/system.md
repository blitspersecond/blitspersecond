# System

Configuration, logging and observability — mostly services, and not
entirely. `services/` is the obvious name for this and not the one it has:
the capture format is a data contract rather than a service, and it belongs
here too.

**The hot path is the distinction.** [The engine](../engine/engine.md) is
what runs to build a frame and land it. This is everything else.

**A metric push happens every frame, and that does not make it hot path.**
The test is removal: stop pushing and the same pixels land, in the same
order, at the same moments. Metrics is a *sink* — nothing it receives feeds
back into building a frame — and so is logging, and so is the capture
recorder. Configuration is the one that is not a sink, and it is read outside
the frame rather than during one.

What a sink costs to reach is still this subject's problem to solve rather
than the engine's to absorb, which is why `Counter` takes a total rather than
an increment and prices the difference in its own docstring.

Not written. What the subject owes:

| | |
|---|---|
| [metrics.md](metrics.md) | *stub* — the instruments |
| logging | the levels, and what the engine logs without being asked |
| configuration | not built; waiting on *Stand up file-backed config*. What is hardcoded today and what it will own |
| the capture format | what `system/frame_pacing/recorder.py` writes, and the v1/v2 compatibility rule |

**Capture methodology is not a chapter.** Taking and reading a frame-pacing
capture is a runbook, useless away from a terminal, so it lives in the tools.
The format is a different question and belongs here; the procedure does not.
