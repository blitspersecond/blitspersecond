from .geometry import Size

#: The internal screen: 640x360, fixed.
#:
#: NOT configuration, and deliberately not on `Config`. The README calls it
#: "a fixed 640x360 internal screen" and every surface in the engine is
#: authored against it -- a glyph grid works back from it to decide how many
#: cells fit, and presentation scales it by whole numbers to whatever window
#: it lands in. A field implying it varies would mislead every reader of it.
#:
#: It lives in `common` because two subsystems need it and must not reach
#: each other for it: `BlitsPerSecond` sizes its display from it, and
#: `graphics.console` divides it by a cell size. The console cannot ask the
#: engine -- the engine imports graphics -- so a copy would have appeared
#: here sooner or later, and 640 spelled twice is 640 that can disagree.
SCREEN = Size(640, 360)
