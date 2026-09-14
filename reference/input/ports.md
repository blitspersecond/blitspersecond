# Ports

Four durable routes, and which pad is on each.

**Not built, and deferred on purpose.** Four people on a sofa is the only
thing that needs durable routes, and no project wants it yet. Building it now
would mean answering its open questions with no consumer to check them
against — which is how the subsystem it replaces went wrong. This page is a
marker so the chapters that stop at this boundary have somewhere to point,
and so the boundary is visible rather than implied.

What it owes, when it exists: the four routes and why four is a cap on routes
and never on pads; that a route may hold a pad or the local keyboard and
pointer; that mapping is explicit and user-managed rather than inferred from
who pressed something first; and why a seat is not a pad — the two were
conflated once and the split is the correction.

**A game does not need this to give four people a controller each.** Iterating
`pads` gives every connected pad, and a `Pad` outlives its connection — the
same fingerprint revives the same object with its id and its history. A game
that assigns pads to players itself keeps those assignments across a flat
battery for free. What Ports would add is the engine remembering the routes
instead, which is worth having and is not urgent.

[pads.md](pads.md) owns physical devices and their identity, and hands over
here. Nothing in the engine imports this today.

The design record is `docs/bps/CONTROLLERS.md`, *Pads and Ports*, with the
rulings in `docs/bps/PADS_PORT.md`, *Prior art: seats were built, promoted,
and deleted*, and the archived arc in `deprecated/player.dead/`. The board
card *Build Ports when a couch game forces it* carries what is open.
