from numpy import arange, float32

SAMPLE_RATE = 48_000
FRAME_SIZE = 400

_SAMPLE_OFFSETS = arange(FRAME_SIZE, dtype=float32)
_SAMPLE_OFFSETS.setflags(write=False)
