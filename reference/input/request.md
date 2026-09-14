# A request

Some questions cannot be answered on the frame they are asked. *What is your
name?* waits for someone to type it. *Which pad are you using?* waits for
someone to pick one up. A **request** is how the engine asks one without
anybody waiting.

```python
name = console.echo(bps.keyboard.input().until(key.RETURN))
who  = bps.pads.identify().until(Button.START)

if name.completed: ...
if who.completed: ...
```

Two exist, and they are one object under different subjects. This chapter is
the lifecycle they share: the four states, how a question is narrowed, who
advances it, and how asking again ends the one before. What a line of typing
reduces is [text.md](text.md)'s; how a pad is resolved is [pads.md](pads.md)'s;
how an echoed line is drawn is [console.md](../graphics/console.md)'s.

## Limits and costs

### The surface

```text
request.state          InputState -- exactly one of four
request.listening      still waiting; the only state an answer can arrive in
request.completed      answered; `value` is the answer
request.interrupted    something else ended it; `reason` says what
request.discarded      the caller abandoned it
request.reason         why it ended, when the player did not end it
request.discard()      abandon it; it stops consuming and keeps nothing

request.until(...)     narrow it, and return self
request.value          what it is for -- defined by each subject, not here
```

The last two are a **convention rather than an inheritance**. `Request` defines
neither, because what a request is *for* differs and the base has no business
guessing. A test asserts both subjects carry both names; nothing enforces it at
runtime, and it looks more like inheritance than it is.

### What refuses

**`until()` refuses the same three things on both**, and narrowing once is the
whole of it:

| | |
|---|---|
| narrowing twice | `RuntimeError` — it already has a terminator / a condition |
| narrowing once it has stopped listening | `RuntimeError` |
| narrowing with the wrong type | `TypeError` |

A second narrowing is a mistake in the caller rather than a change of mind
worth honouring: the request is already consuming against the first one, and
quietly swapping it would change what a question means partway through asking
it.

`Identification.value` before completion raises `RuntimeError`.
`TextInput.value` never refuses — see *`value` is each subject's own*.

### The three reasons

`REPLACED` when a newer question of the same kind supersedes this one,
`CLOSED` when the subsystem holding it shuts down, `DISCARDED` when the caller
abandons it. They are plain strings, and the set is small because only the
engine sets them.

## Four states, and exactly one is true

`LISTENING` is where a request begins and the only state in which an answer can
arrive. The other three are terminal, and which one it reached says who ended
it: the **player** answered (`COMPLETED`), the **engine** ended it
(`INTERRUPTED`, with a reason), or the **caller** walked away (`DISCARDED`).

The four predicates exist so a game never compares enum members. `state` is
there for the case that wants the value itself.

A terminal request stops consuming. It is inert rather than removed, so a
reference held anywhere stays valid and keeps telling the truth about what
happened to it.

## `until()` narrows, and hands back the request

Narrowing is optional and returns the request, so the call site reads as one
phrase rather than three statements:

```python
bps.pads.identify().until(Button.START | Button.ACCEPT)
```

Without it, any deliberate use answers. With it, only the presses named do.

**The two subjects narrow by different things**, which is why the base does not
define the verb: a line of typing is narrowed by the key that finishes it, and
an identification by which buttons count. One takes a key symbol, the other a
`Button` condition, and neither could be written generically without becoming
an argument that means nothing until you know which request you are holding.

## Advanced by the cut, never polled

Nothing blocks and the caller polls nothing. A request is fed by the engine's
per-frame cut, which happens before the game is handed control.

That ordering is what makes a request safe to read casually. Its value is
settled for the whole of a tick, so it cannot change halfway down a function
reading it, and reading it twice in one frame gives the same answer both times.

## `value` is each subject's own, and they disagree

Both requests carry a `value`. What it means is not the same, and the
difference is the reason the base stays out of it.

`TextInput.value` is readable **while listening**. A line half typed is a
perfectly good partial answer — it is what `console.echo` draws every frame —
so the value is the line as it currently stands, and completion only means
nothing more will be added.

`Identification.value` **raises until completion**. There is no partial answer
to a question about which pad somebody picked up; either one has been used or
none has. Returning `None` would be worse than raising: a game checks
`completed` first, so reading early is a mistake in the caller rather than a
pad that failed to exist, and a `None` would propagate somewhere else before it
broke.

So a base class defining `value` would have to pick one of those and be wrong
for the other subject.

## One question at a time, per device

**The device holds the question**, and it holds exactly one. The keyboard keeps
the line being typed; `Pads` keeps the identification, because *which pad* is a
question about the set rather than about any one of them.

Asking again supersedes the previous one with reason `REPLACED`:

```python
first  = bps.keyboard.input()
second = bps.keyboard.input()   # first is now INTERRUPTED, reason REPLACED
```

A question that has been asked again is a different question, and the old one
should not quietly answer the new one. Superseding says so where silently
keeping both would let two reducers consume the same keystrokes.

**Holders do not interact.** A line of typing and a pad identification are open
at the same time without either affecting the other, because the constraint is
per device rather than global. A game asking a pad question does not disturb a
console line that is still being typed.

## Two subjects, one shape

The lifecycle is written once because two subjects arrive at it independently.

`TextInput` and `Identification` are different in every way that a reader would
expect to matter — one reduces characters from a keyboard, the other watches
six controls across an unbounded set of pads — and they agree on the four
states, the narrowing verb and its three refusals, and supersede-on-reasking.
**Two subjects converging independently on one shape is the signal to write it
once**, and a reader who has understood either has understood the other.

What the base owns is the state machine, and nothing else. That boundary is
what keeps it honest: everything the two disagree about — what narrows them,
what their value means, what they consume — stayed with them.

## What it is not

**Not a future, and not a promise.** This is the shape a reader would otherwise
assume, because a non-blocking question that completes later looks exactly like
one. There is no callback, no scheduler, no `await`, and nothing to attach a
continuation to.

The difference is not stylistic. A callback fires when the answer arrives —
which is to say at an arbitrary point, on whatever thread the transport
delivered on, in the middle of whatever the game was doing. A request is
advanced on the frame boundary and read whenever the game gets round to it,
which is what makes its value stable for a tick and what lets it be read from
anywhere without a lock or a re-entrancy rule. The engine already has a
boundary that every device settles onto; a request rides it rather than
inventing a second way to arrive.

**Not an event handler.** A request is asked, not subscribed. It ends, and it
cannot be re-armed — a second question is a second object.

