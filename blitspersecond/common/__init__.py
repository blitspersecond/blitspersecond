"""Shared language: the types and behaviours the whole engine speaks in.

**The test for living here is having no dependency outside the standard
library.** Not "is it small" and not "is it used twice" -- those are why you
might WANT something here; this is what lets it BE here. A module in `common`
imports stdlib and its own siblings, and nothing else in `blitspersecond`
ever.

That is what makes any of it safe to reach for at any moment. Nothing in this
package can be half-built when you touch it, because there is no other part
of the engine it could be waiting on: no window, no GL context, no config, no
singleton that had to be constructed in the right order first. So `Size` is
available before there is a display to measure, `fps` before there is a clock
to run at, and `CacheableResource` before there is anything to load. Whoever
needs one of these first simply takes it.

The engine leans on that directly. `Clock` is built before `Metrics` so the
fold ratio can be read from it rather than passed in and cached wrong; that
works only because a rate is `common.fps` and needs no hardware. The same
move gives `Layers` to `Display` as a required argument instead of a rule
someone has to remember. Ordering stops being a hazard when the thing you
need first has nothing underneath it.

It is shared with USERS, not only within the engine. A game writes
`bps.framerate is fps.SIXTY`, unpacks a `Size`, and catches an
`InvalidTransparencyError`. These are public vocabulary, which is the other
half of why they are here rather than tucked inside whichever package
happened to want one first -- `Location` describes a mouse position as
readily as a sprite's, so it cannot belong to `graphics`.

  cacheable_resource.py  loaded from a file at most once, and interned. The
                         pattern `Image` and friends inherit.
  environment.py         reading BPS_* flags out of the environment.
  errors.py              exception types the engine raises at users.
  framerate.py           `fps` -- the rates a game may be authored at.
  geometry.py            Size, Location, Rect: immutable, unpackable,
                         and equal to the plain tuples they look like.
  paths.py               PACKAGE_ROOT, for reaching bundled assets.
  screen.py              SCREEN -- the fixed 640x360 internal screen.
  singleton_meta.py      the metaclass behind the engine's one-of-a-kind
                         objects.

Anything that grows an import from elsewhere in `blitspersecond` has stopped
being common and wants moving out, whatever its size.
"""

from .cacheable_resource import CacheableResource
from .environment import environment_flag
from .errors import InvalidTransparencyError
from .framerate import fps
from .geometry import Location, Rect, Size
from .paths import PACKAGE_ROOT
from .screen import SCREEN
from .singleton_meta import SingletonMeta

__all__ = [
    "PACKAGE_ROOT",
    "SCREEN",
    "CacheableResource",
    "InvalidTransparencyError",
    "Location",
    "Rect",
    "Size",
    "SingletonMeta",
    "environment_flag",
    "fps",
]
