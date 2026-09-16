"""`render` and `render_wav`: the engine with nothing pacing it.

**Offline is not a mode.** The engine cannot tell it is being rendered rather
than played, which is the property that makes this a testing surface at all
-- so the tests check that a render produces exactly what stepping `advance()`
by hand produces, block for block.

**WAV output is tested by reading the file back with the stdlib `wave`
module**, not by inspecting the array we passed in. The point of writing a
file is that something else can open it, so asserting on our own intermediate
would skip the only part that could be wrong.
"""

import wave

import numpy as np
import pytest

from blitspersecond.audio.constants import BLOCK_SIZE, SAMPLE_RATE
from blitspersecond.audio.synth.engine import AudioEngine
from blitspersecond.audio.synth.mixing_desk import MixingDesk
from blitspersecond.audio.transport.offline import render, render_wav, write_wav
from blitspersecond.audio.synth.operators import Operator
from blitspersecond.audio.synth.program import Program

#: Exactly one block. Spelled as a name because a plain "0.01" is 480
#: samples, which rounds UP to two blocks -- the kind of number that
#: makes a length assertion look wrong when the test was.
ONE_BLOCK = BLOCK_SIZE / SAMPLE_RATE


def _desk(engine) -> MixingDesk:
    """The engine's default output, narrowed.

    `AudioEngine.output` is typed `Stage` because it can be replaced with
    any Stage, so the obvious `engine.output.connect(voice)` does not
    type-check even though a fresh engine's output is always a desk.
    """
    output = engine.output
    assert isinstance(output, MixingDesk)
    return output


class _Ramp(Operator):
    """Counts blocks, so the ORDER of rendered blocks is visible in the data."""

    def __init__(self):
        self.blocks = 0

    def process(self, bus, /):
        self.blocks += 1
        bus.current.data.fill(self.blocks / 1000.0)
        bus.current.quiescent = False
        return bus


class _Loud(Operator):
    def process(self, bus, /):
        bus.current.data[:] = np.linspace(-2.0, 2.0, BLOCK_SIZE, dtype=np.float32)
        bus.current.quiescent = False
        return bus


@pytest.fixture
def engine() -> AudioEngine:
    engine = AudioEngine()
    engine.output = engine.source(Program(_Ramp))
    return engine


# -- whole blocks ------------------------------------------------------


def test_a_render_spans_whole_blocks(engine):
    assert render(engine, ONE_BLOCK).shape == (BLOCK_SIZE,)


def test_a_duration_between_boundaries_rounds_up(engine):
    """Half a block gets a whole one, not a truncated engine state."""
    assert render(engine, 0.5 * ONE_BLOCK).shape == (BLOCK_SIZE,)
    assert render(engine, 1.5 * ONE_BLOCK).shape == (2 * BLOCK_SIZE,)


def test_one_second_is_one_hundred_and_twenty_blocks(engine):
    assert render(engine, 1.0).shape == (SAMPLE_RATE,)


def test_a_zero_length_render_produces_nothing(engine):
    assert render(engine, 0.0).shape == (0,)


def test_the_clock_advances_with_the_render(engine):
    render(engine, 1.0)
    assert engine.clock == SAMPLE_RATE


def test_blocks_arrive_in_order(engine):
    samples = render(engine, 3 * ONE_BLOCK)
    assert samples[0] == pytest.approx(0.001)
    assert samples[BLOCK_SIZE] == pytest.approx(0.002)
    assert samples[2 * BLOCK_SIZE] == pytest.approx(0.003)


def test_rendering_matches_stepping_the_engine_by_hand():
    """Offline is another driver of the same boundary, not a mode."""
    stepped = AudioEngine()
    stepped.output = stepped.source(Program(_Ramp))
    by_hand = np.concatenate(
        [np.array(stepped.advance().current.data) for _ in range(4)]
    )

    rendered = AudioEngine()
    rendered.output = rendered.source(Program(_Ramp))

    assert np.array_equal(render(rendered, 4 * ONE_BLOCK), by_hand)


# -- shape follows the output Stage ------------------------------------


def test_a_mono_output_stays_mono(engine):
    """So a test can look at one Operator without a pan law in the way."""
    assert render(engine, 0.01).ndim == 1


def test_a_desk_renders_as_two_channels():
    engine = AudioEngine()
    _desk(engine).connect(engine.source(Program(_Ramp)))
    samples = render(engine, 0.01)
    assert samples.ndim == 2
    assert samples.shape[1] == 2


def test_rendered_samples_are_float32(engine):
    assert render(engine, 0.01).dtype == np.float32


# -- claiming the engine -----------------------------------------------


def test_a_render_claims_the_engine_and_gives_it_back(engine):
    assert engine._running is False
    render(engine, 0.01)
    assert engine._running is False


def test_an_engine_already_being_driven_cannot_be_rendered(engine):
    engine._running = True
    with pytest.raises(RuntimeError):
        render(engine, 0.01)


def test_a_failed_render_does_not_leave_the_engine_claimed():
    """Otherwise one bad graph makes the engine permanently unplayable."""

    class _Broken(Operator):
        def process(self, bus, /):
            raise ZeroDivisionError("a graph that raises")

    engine = AudioEngine()
    engine.output = engine.source(Program(_Broken))

    with pytest.raises(ZeroDivisionError):
        render(engine, 0.01)

    assert engine._running is False


def test_topology_is_frozen_during_a_render():
    """The same claim the device Driver takes, checked the same way."""
    engine = AudioEngine()
    seen = []

    class _Peek(Operator):
        def process(self, bus, /):
            seen.append(engine._running)
            return bus

    engine.output = engine.source(Program(_Peek))
    render(engine, 0.01)

    assert all(seen)


@pytest.mark.parametrize(
    "seconds, error",
    [
        (-1.0, ValueError),
        (float("nan"), ValueError),
        (float("inf"), ValueError),
        ("1.0", TypeError),
        (True, TypeError),
    ],
)
def test_a_duration_must_be_a_finite_non_negative_number(engine, seconds, error):
    with pytest.raises(error):
        render(engine, seconds)


def test_render_refuses_anything_that_is_not_an_engine():
    with pytest.raises(TypeError):
        render(object(), 1.0)  # type: ignore[arg-type]


# -- writing a file ----------------------------------------------------


def _read_back(path):
    with wave.open(str(path), "rb") as wav:
        return (
            wav.getnchannels(),
            wav.getsampwidth(),
            wav.getframerate(),
            np.frombuffer(wav.readframes(wav.getnframes()), dtype="<i2"),
        )


def test_a_mono_file_reads_back_as_one_channel_at_the_engine_rate(engine, tmp_path):
    path = tmp_path / "mono.wav"
    render_wav(engine, path, ONE_BLOCK)

    channels, width, rate, frames = _read_back(path)
    assert (channels, width, rate) == (1, 2, SAMPLE_RATE)
    assert len(frames) == BLOCK_SIZE


def test_a_stereo_file_reads_back_interleaved(tmp_path):
    engine = AudioEngine()
    source = engine.source(Program(_Ramp))
    _desk(engine).connect(source)
    engine.schedule([(0, engine.output, source, {"pan": -1.0})])
    path = tmp_path / "stereo.wav"

    render_wav(engine, path, ONE_BLOCK)

    channels, _width, _rate, frames = _read_back(path)
    assert channels == 2
    assert len(frames) == BLOCK_SIZE * 2
    # Hard left: every odd (right) sample is silent.
    assert np.all(frames[1::2] == 0)
    assert np.any(frames[0::2] != 0)


def test_writing_clips_rather_than_rescaling(tmp_path):
    """A render that came out too hot should sound like it did."""
    engine = AudioEngine()
    engine.output = engine.source(Program(_Loud))
    path = tmp_path / "hot.wav"

    samples = render_wav(engine, path, 0.01)

    _channels, _width, _rate, frames = _read_back(path)
    assert samples.min() < -1.0 and samples.max() > 1.0
    assert frames.min() == -32767
    assert frames.max() == 32767


def test_render_wav_hands_the_samples_back_too(engine, tmp_path):
    samples = render_wav(engine, tmp_path / "out.wav", ONE_BLOCK)
    assert samples.shape == (BLOCK_SIZE,)


def test_write_wav_refuses_a_shape_that_is_not_mono_or_stereo(tmp_path):
    with pytest.raises(ValueError):
        write_wav(tmp_path / "bad.wav", np.zeros((10, 3), dtype=np.float32))
    with pytest.raises(ValueError):
        write_wav(tmp_path / "bad.wav", np.zeros((2, 2, 2), dtype=np.float32))
