from numpy import count_nonzero, float32

from blitspersecond.audio.common import FRAME_SIZE
from blitspersecond.audio.engine import AudioEngine


def test_audio_engine_has_a_silent_terminal_stage_from_construction():
    engine = AudioEngine()

    output = engine.advance().current.data

    assert output.shape == (FRAME_SIZE, 2)
    assert output.dtype == float32
    assert count_nonzero(output) == 0
    assert engine.clock == FRAME_SIZE
