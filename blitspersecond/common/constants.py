# The engine's fixed internal screen: 640 wide, 360 tall, at 60Hz. Not
# config -- these are the shape BPS *is*, which is why they left Config in
# the 2026-08-25 audit. Written one-per-line rather than as a tuple unpack:
# `HEIGHT, WIDTH = 640, 360` silently bound them the wrong way round, and a
# transposed screen only shows up once a window opens portrait.
WIDTH = 640
HEIGHT = 360
FRAMERATE = 60
