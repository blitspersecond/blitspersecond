from tests.examples.engine_midi import presentation_fade


def test_presentation_fade_envelope():
    lead_time = 3.0
    playback_end = 20.0
    fade_in = 3.0
    fade_out = 10.0

    assert presentation_fade(0.0, lead_time, playback_end, fade_in, fade_out) == 0.0
    assert presentation_fade(3.0, lead_time, playback_end, fade_in, fade_out) == 0.0
    assert presentation_fade(4.5, lead_time, playback_end, fade_in, fade_out) == 0.5
    assert presentation_fade(6.0, lead_time, playback_end, fade_in, fade_out) == 1.0
    assert presentation_fade(11.0, lead_time, playback_end, fade_in, fade_out) == 1.0
    assert presentation_fade(16.0, lead_time, playback_end, fade_in, fade_out) == 0.5
    assert presentation_fade(20.0, lead_time, playback_end, fade_in, fade_out) == 0.1
    assert presentation_fade(21.0, lead_time, playback_end, fade_in, fade_out) == 0.0


def test_zero_length_fades_are_immediate():
    assert presentation_fade(3.0, 3.0, 20.0, 0.0, 0.0) == 1.0
    assert presentation_fade(20.0, 3.0, 20.0, 0.0, 0.0) == 1.0
    assert presentation_fade(21.0, 3.0, 20.0, 0.0, 0.0) == 0.0
