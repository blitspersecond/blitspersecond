# Out of the graph

Two transports, and **the only difference is what paces them.** `Driver` hands
each block to PortAudio and blocks until the sound card has taken it; `render`
takes blocks as fast as the CPU can make them. Neither mentions a Stage, an
operator or a register — both call `advance()` and deal with what comes back,
which is the whole of this package's dependency on synthesis.

**None of it is user-side.** A game is handed an `AudioEngine` that is already
being driven; it never opens a stream, picks a device or starts a thread.
An import that says `transport` is a tool reaching for audio deliberately.

## The blocking write is the clock

    advance one block  →  write it  →  repeat

PortAudio's blocking `write` returns when the device has room for more, so
that loop is paced by the hardware itself. **No timer, no sleep, no deadline
arithmetic** — the sound card decides how fast audio time moves and
backpressure does the rest.

Synthesis happens on this thread rather than inside a PortAudio callback, so a
slow block **delays** audio instead of corrupting it, and it stays off the
sixteen milliseconds the simulation and the picture share. This is the
engine's single exception to polling at the frame cut, and the exception is
the whole reason audio gets a thread.

**The engine block and the host period are deliberately independent.** The
stream opens with `blocksize=0`, leaving the period to whichever of PipeWire,
ALSA, WASAPI, USB or Bluetooth is on the other end, so one write can carry one
whole 400-sample block without forcing the device to adopt that size.

```
samplerate=48000  channels=2  dtype=float32  blocksize=0
```

## Startup is a readiness handshake

`start()` returns only once the device is open, the stream is running, and it
has accepted the first block — which is rendered **before** the stream starts,
so the device is never asked for audio that does not exist yet.

```
before start   running=False   latency=None
after start    running=True    latency=12ms   device=FakeAPI: Fake Out
```

Device failures come back to the caller with a traceback rather than
disappearing into a background thread and leaving silence to explain itself.
**A failed start releases the engine**, so a graph is not left permanently
unplayable by a device that would not open.

Starting the driver is also what **freezes the graph**: from the moment
`start()` returns, building or connecting a Stage raises. Calling `start()`
again is a no-op; a closed driver cannot be restarted.

```
start() twice        -> fine, the second returns immediately
start() after close  -> RuntimeError: cannot restart a closed audio Driver
```

**There is no pause or resume, deliberately.** Once started the stream runs
until `close()`, through suspension, backgrounding and everything else. A game
being paused is not a reason to tear down an audio device, and reopening one
is where the clicks live. `close()` is terminal teardown at process exit,
beside the display closing.

The worker publishes its active stream, and `close()` aborts that stream before
joining the thread. The abort is intentionally issued from the closing thread:
it releases a `write()` whose endpoint has stopped consuming rather than
leaving orderly process teardown parked inside PortAudio. A deterministic
blocked-write test covers that ordering; ordinary WASAPI open, write and close
were also exercised on Windows without an xrun.

**A real endpoint removal does not block, so it does not reach that path.**
Powering off a Bluetooth headset mid-tone raised
`AUDCLNT_E_DEVICE_INVALIDATED` out of the write after 495 blocks and zero
xruns; the worker exited, `engine._running` went false, and `close()` returned
in 0.0ms with no thread left to join. The deterministic fake remains the only
coverage of the abort-before-join ordering, because WASAPI invalidates the
client rather than leaving a write parked.

**A stream does not follow the default, and the process cannot see a new
endpoint.** A tone opened on the speakers kept playing there for 30 seconds
while the headset returned and took the system default back -- correct, since
a stream is bound to the index chosen at open. But the in-process device list
never changed either: PortAudio enumerates at initialise and does not look
again, so the headset stayed invisible until `sounddevice._terminate()` and
`_initialize()`. Any recovery onto a replacement device therefore needs
PortAudio torn down first, which makes it a process-wide step rather than
something `Driver` can do alone. Whether to recover at all is on the board;
today an invalidated endpoint is terminal for the run.

## A mono output is refused at the boundary

The device Stage must be a `MixingDesk`. The driver clips the settled stereo
block into one reusable buffer and writes it unchanged — it **never
manufactures a second channel from one**, because "both speakers get the same
thing" is a decision the graph should make rather than the transport.

```
AudioEngine returned shape (400,), expected (400, 2)
```

Selecting a deliberately mono output is still useful offline, where it is what
lets a test look at one operator's signal with no pan law in the way.

## Which device opens

**Linux and macOS take PortAudio's own default**, returned as `None`. On the
tested Linux path that reaches the shared PipeWire sink through PortAudio's
ALSA view, which is the right answer: the engine mixes into the same sink as
everything else on the desktop rather than seizing the card or bypassing the
user's volume.

**Windows opens WASAPI's own current default output.** PortAudio's overall
default there can be a legacy MME endpoint even when the same endpoint is
available through WASAPI, so falling through to `None` silently picks the
worse host API. **A wrong-but-working device reaches a player as bad latency
and never as an error**, so the engine enumerates the host APIs, selects
`Windows WASAPI`, and uses that host's `default_output_device`. If WASAPI or
its default is absent, startup refuses rather than falling back.

Measured on the native Windows test machine: PortAudio's process-wide default
was device 5, `Headphones (MAJOR III BLUETOOTH)` through MME; WASAPI named the
same endpoint as its default at device 20. The engine chooses 20.

## Diagnostics

| | |
|---|---|
| `xruns` | underflows the host reported: audio it wanted and did not have. **This is the number that means trouble** |
| `worst_write` | the longest a single blocking write took |
| `blocks_written` | blocks handed to the device |
| `latency` | what the host reported, in seconds — not what was asked for |
| `epoch` | `perf_counter()` when the stream started, for correlating with the simulation clock from outside. The engine never reads it |
| `stats()` | all of the above as one line |

**`worst_write` is not a stall gauge, and high is not bad.** It measures how
long the device kept *us* waiting, which is the backpressure doing its job. A
healthy stream sits near one block's worth or above. **A `worst_write`
consistently near zero is the bad sign** — it means the engine is always
behind and the device is never full, which is what precedes xruns.

## The offline renderer

`render()` is **another driver of the same boundary, not a mode the engine is
put into.** It pulls `advance()` in a loop with nothing pacing it; the engine
cannot tell the difference and behaves identically, so a graph checked here is
the graph the device will play.

That is what makes it the testing surface: deterministic, no hardware, and a
fraction of the time it represents. `render_wav()` puts the same audio
somewhere you can listen to it, which is the only way some faults are ever
noticed.

**Results span whole blocks**, exactly as the engine clock does, so a duration
landing between boundaries rounds **up**:

| asked | got |
|---|---|
| 0 ms | 0 samples |
| 8.333 ms | 400 samples, 1 block |
| 8.354 ms | 800 samples, 2 blocks |

A mono output comes back `(n,)` and a desk's comes back `(n, 2)`.

**One engine cannot be rendered and played at once.** Both would be advancing
the same clock and mutating the same pool, so the second raises
`AudioEngine is already driven` — and the claim is released even if a graph
raises mid-render.

## Writing a file

`write_wav` writes mono or stereo float32 as signed 16-bit PCM at 48 kHz.

**Clipped at the format's limit, never normalised.** Sixteen-bit PCM has no
room above unity so something has to happen there, but rescaling would quietly
change the mix — and a signal driven past unity may well be deliberate, which
is what `Tanh`, `Clip` and `Crush` are in the vocabulary for. The file gets
what the graph produced, squared off where the format runs out:

```
in   [ 0.0   0.5   1.5  -1.5 ]
out  [   0  16383  32767  -32767 ]      of ±32767
```

Truncation toward zero costs at most half an LSB, about -90 dBFS.

## Where the engine drives it

One `AudioEngine` and one `Driver` are built in `BlitsPerSecond.__init__`,
neither touching a device. `Loop.run` calls `start()` at the top — after the
game has built its graph, because starting freezes it, and before the
presentation baselines exist, because opening a device blocks and would
otherwise read as a stalled first frame. Audio comes down first in teardown,
before the window. [audio.md](audio.md) has the sequence.

`play(engine, seconds)` runs a standalone engine on the device for a finite
wall-clock time, for hearing something without building a game around it. The
engine's clock is still advanced by the device; the sleep only decides when to
stop asking.

## Limits

| | |
|---|---|
| threads | one, and this is it |
| device output | stereo `(400, 2)` float32; mono is refused |
| stream | 48 kHz, `blocksize=0`, latency requested as `"low"` |
| platforms | Linux and macOS take PortAudio's default; Windows takes WASAPI's default output |
| pause / resume | none, by design |
| restart after `close()` | `RuntimeError` |
| rendering a driven engine | `RuntimeError`, and the reverse too |
| `render()` duration | finite and non-negative; rounds up to whole blocks |
| WAV output | 16-bit PCM at 48 kHz, mono or stereo; three channels raise |
| `close()` | aborts the active stream, then joins the worker |
