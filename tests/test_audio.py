"""Contracts for the demand-pulled Stage audio engine."""

import wave
from types import MappingProxyType

import numpy as np
import pytest

from blitspersecond.audio.common import (
    Bus,
    FRAME_SIZE,
    Frame,
    RegisterScope,
    RegisterSpec,
    SAMPLE_RATE,
)
from blitspersecond.audio.common.bus import FramePool
from blitspersecond.audio.driver import render_wav
from blitspersecond.audio.engine import AudioEngine, Program
from blitspersecond.audio.instruments import (
    clap,
    cowbell,
    cymbal,
    drum,
    hat,
    kick,
    metal_drum,
    noise_drum,
    shaker,
    snare,
)
from blitspersecond.audio.operators import (
    BandPass,
    Clip,
    Delay,
    Envelope,
    Crush,
    Gain,
    LowPass,
    Duck,
    Echo,
    HighPass,
    Metal,
    Mix,
    Noise,
    Operator,
    PCM,
    Pluck,
    Pulse,
    Reverb,
    Resonator,
    RingMod,
    Saw,
    Sine,
    Square,
    Tanh,
    Triangle,
)
from blitspersecond.audio.operators.delay import MAX_DELAY_TICKS
from blitspersecond.resources import ResourceManager, SoundSpec


class Constant(Operator):
    def __init__(self, value=1.0):
        self.value = value

    def process(self, bus):
        bus.current.data.fill(self.value)
        return bus


class Ramp(Operator):
    def __init__(self):
        self.next = 0

    def process(self, bus):
        bus.current.data[:] = np.arange(
            self.next,
            self.next + FRAME_SIZE,
            dtype=np.float32,
        )
        self.next += FRAME_SIZE
        return bus


class UnitRamp(Operator):
    def process(self, bus):
        bus.current.data[:] = np.linspace(
            0.0,
            1.0,
            FRAME_SIZE,
            dtype=np.float32,
        )
        return bus


class FrequencySweep(Operator):
    def process(self, bus):
        bus.current.data[:] = np.linspace(
            220.0,
            880.0,
            FRAME_SIZE,
            dtype=np.float32,
        )
        return bus


class CountingSource(Operator):
    def __init__(self):
        self.calls = 0

    def process(self, bus):
        self.calls += 1
        bus.current.data.fill(1.0)
        return bus


class Impulse(Operator):
    def __init__(self):
        self.started = False

    def process(self, bus):
        bus.current.data.fill(0.0)
        if not self.started:
            bus.current.data[0] = 1.0
            self.started = True
        return bus


class LaneBias(Operator):
    scope = RegisterScope.LANE

    def __init__(self, bias=0.25):
        self.registers = MappingProxyType(
            {"bias": RegisterSpec(float(bias), unit="linear")}
        )
        self._clock = 0
        self._registers = MappingProxyType({})
        self._writes = ()

    def control(self, clock, registers, writes):
        self._clock = clock
        self._registers = registers
        self._writes = writes

    def process(self, bus):
        bias = self._registers["bias"]
        start = 0
        for write in self._writes:
            stop = write.timestamp - self._clock
            bus.current.data[start:stop] += bias
            for _name, value in write.values:
                bias = value
            start = stop
        bus.current.data[start:] += bias
        return bus


def render(engine, frames):
    return np.concatenate(
        [engine.advance().current.data.copy() for _ in range(frames)]
    )


def dominant_frequency(samples):
    spectrum = np.abs(np.fft.rfft(samples * np.hanning(len(samples))))
    frequencies = np.fft.rfftfreq(len(samples), 1.0 / SAMPLE_RATE)
    return frequencies[np.argmax(spectrum)]


def test_default_processing_grid_and_silent_output():
    engine = AudioEngine()

    assert SAMPLE_RATE == 48_000
    assert FRAME_SIZE == 400
    assert engine.clock == 0
    assert engine.frame == 0
    assert np.array_equal(
        engine.advance().current.data,
        np.zeros((FRAME_SIZE, 2)),
    )
    assert engine.clock == FRAME_SIZE
    assert engine.frame == 1


def test_sine_frequency():
    engine = AudioEngine()
    source = engine.source(Program(Sine))
    engine.output = source
    engine.schedule([(0, source, {"frequency": 440.0})])

    samples = render(engine, SAMPLE_RATE // FRAME_SIZE)

    assert dominant_frequency(samples) == pytest.approx(440.0, abs=1.0)


def test_sine_frequency_write_is_sample_accurate_and_phase_continuous():
    engine = AudioEngine()
    source = engine.source(Program(Sine))
    engine.output = source
    engine.schedule(
        [
            (0, source, {"frequency": 440.0}),
            (200, source, {"frequency": 880.0}),
        ]
    )

    actual = engine.advance().current.data.copy()
    first_step = 2.0 * np.pi * 440.0 / SAMPLE_RATE
    second_step = 2.0 * np.pi * 880.0 / SAMPLE_RATE
    expected = np.concatenate(
        (
            np.sin(np.arange(200) * first_step),
            np.sin(200 * first_step + np.arange(200) * second_step),
        )
    ).astype(np.float32)

    assert np.allclose(actual, expected, atol=2e-5)


def test_square_is_a_continuous_frequency_controlled_source():
    engine = AudioEngine()
    source = engine.source(Program(Square))
    engine.output = source
    engine.schedule([(0, source, {"frequency": SAMPLE_RATE / 8})])

    samples = engine.advance().current.data.copy()

    expected = np.tile((1.0, 1.0, 1.0, 1.0, -1.0, -1.0, -1.0, -1.0), 50)
    assert np.array_equal(samples, expected)


def test_triangle_is_a_continuous_frequency_controlled_source():
    engine = AudioEngine()
    source = engine.source(Program(Triangle))
    engine.output = source
    engine.schedule([(0, source, {"frequency": SAMPLE_RATE / 8})])

    samples = engine.advance().current.data.copy()

    expected = np.tile(
        (1.0, 0.5, 0.0, -0.5, -1.0, -0.5, 0.0, 0.5),
        50,
    )
    assert np.array_equal(samples, expected)


def test_seeded_noise_is_deterministic_without_allocating_an_output_frame():
    pool = FramePool()
    first = Bus(pool)
    second = Bus(pool)

    Noise(7).process(first)
    Noise(7).process(second)

    assert np.array_equal(first.current.data, second.current.data)
    assert np.all(first.current.data >= -1.0)
    assert np.all(first.current.data < 1.0)


def test_apply_stage_runs_one_program_across_two_connected_stage_outputs():
    engine = AudioEngine()
    left = engine.source(Program(lambda: Constant(2.0)))
    right = engine.source(Program(lambda: Constant(3.0)))
    product = engine.apply(Program(RingMod))
    product.connect(left)
    product.connect(right)
    engine.output = product

    samples = engine.advance().current.data

    assert np.all(samples == 6.0)


def test_front_rack_operators_process_one_owned_lane_in_sequence():
    engine = AudioEngine()
    source = engine.source(Program(Constant))
    rack = engine.composite(
        Program(
            lambda: LowPass(6_000.0),
            lambda: Tanh(7.0),
            lambda: Crush(9, 16_000.0),
        )
    )
    rack.connect(source)
    engine.output = rack

    samples = engine.advance().current.data

    assert np.all(np.isfinite(samples))
    assert np.max(np.abs(samples)) <= 1.0
    assert len(np.unique(samples)) < FRAME_SIZE


def test_high_pass_and_band_pass_select_the_expected_frequency_regions():
    def filtered(operator):
        engine = AudioEngine()
        low = engine.source(Program(Sine))
        middle = engine.source(Program(Sine))
        high = engine.source(Program(Sine))
        rack = engine.composite(Program(operator))
        rack.connect(low)
        rack.connect(middle)
        rack.connect(high)
        engine.output = rack
        engine.schedule(
            [
                (0, low, {"frequency": 200.0}),
                (0, middle, {"frequency": 1_000.0}),
                (0, high, {"frequency": 4_000.0}),
            ]
        )
        return render(engine, SAMPLE_RATE // FRAME_SIZE)

    def magnitude(samples, frequency):
        spectrum = np.abs(np.fft.rfft(samples * np.hanning(len(samples))))
        bins = np.fft.rfftfreq(len(samples), 1.0 / SAMPLE_RATE)
        return spectrum[np.argmin(np.abs(bins - frequency))]

    high_passed = filtered(lambda: HighPass(1_000.0))
    assert magnitude(high_passed, 4_000.0) > 10 * magnitude(
        high_passed, 200.0
    )

    band_passed = filtered(lambda: BandPass(1_000.0, q=4.0))
    middle = magnitude(band_passed, 1_000.0)
    assert middle > 10 * magnitude(band_passed, 200.0)
    assert middle > 5 * magnitude(band_passed, 4_000.0)


def test_high_pass_cutoff_write_changes_the_active_filter_at_its_timestamp():
    engine = AudioEngine()
    source = engine.source(Program(Sine))
    filtered = engine.composite(Program(lambda: HighPass(100.0)))
    filtered.connect(source)
    engine.output = filtered
    engine.schedule(
        [
            (0, source, {"frequency": 1_000.0}),
            (SAMPLE_RATE // 2, filtered, {"cutoff": 5_000.0}),
        ]
    )

    samples = render(engine, SAMPLE_RATE // FRAME_SIZE)
    before = samples[SAMPLE_RATE // 4 : SAMPLE_RATE // 2]
    after = samples[3 * SAMPLE_RATE // 4 :]

    assert np.sqrt(np.mean(before**2)) > 10 * np.sqrt(np.mean(after**2))


def test_clip_is_in_place_and_uses_sample_accurate_live_level():
    engine = AudioEngine()
    source = engine.source(Program(lambda: Constant(2.0)))
    clipped = engine.composite(Program(Clip))
    clipped.connect(source)
    engine.output = clipped
    engine.schedule([(200, clipped, {"clip_level": 0.5})])

    samples = engine.advance().current.data.copy()

    assert np.array_equal(samples[:200], np.ones(200))
    assert np.array_equal(samples[200:], np.full(200, 0.5))
    with pytest.raises(ValueError, match="at least"):
        engine.schedule([(0, clipped, {"clip_level": -1.0})])


def test_duck_uses_a_second_stage_as_control_without_mixing_it():
    engine = AudioEngine()
    signal = engine.source(Program(Constant))
    control = engine.source(Program(Constant))
    ducked = engine.apply(Program(lambda: Duck(1.0, 0.0, 0.0)))
    ducked.connect(signal)
    ducked.connect(control)
    engine.output = ducked

    samples = engine.advance().current.data

    assert np.all(samples == 0.0)


def test_feedback_echo_uses_its_prepared_delay_memory_sample_accurately():
    engine = AudioEngine()
    source = engine.source(Program(Impulse))
    echo = engine.composite(Program(lambda: Echo(5, feedback=0.5, mix=1.0)))
    echo.connect(source)
    engine.output = echo

    samples = engine.advance().current.data

    expected = np.zeros(FRAME_SIZE, dtype=np.float32)
    expected[0] = 1.0
    echoes = np.arange(0, FRAME_SIZE - 5, 5)
    expected[5::5] = 0.5**np.arange(len(echoes))
    assert np.array_equal(samples, expected)


def test_comb_reverb_retains_a_tail_after_an_impulse():
    engine = AudioEngine()
    source = engine.source(Program(Impulse))
    reverb = engine.composite(Program(lambda: Reverb(1.0)))
    reverb.connect(source)
    engine.output = reverb

    samples = render(engine, 5)

    assert samples[0] == 1.0
    assert np.any(samples[FRAME_SIZE:] != 0.0)


def test_schedule_anchors_a_whole_batch_to_the_first_unresolved_frame():
    engine = AudioEngine()
    source = engine.source(Program(Sine))
    engine.output = source
    engine.advance()

    engine.schedule(
        [
            (0, source, {"frequency": 440.0}),
            (600, source, {"frequency": 880.0}),
        ]
    )

    first = engine.writes
    engine.advance()
    second = engine.writes

    assert [write.timestamp for write in first] == [FRAME_SIZE]
    assert [write.timestamp for write in second] == [FRAME_SIZE + 600]


def test_schedule_cannot_reopen_a_frame_after_its_writes_are_resolved():
    engine = AudioEngine()
    source = engine.source(Program(Sine))
    engine.output = source
    assert engine.writes == ()

    engine.schedule([(0, source, {"frequency": 440.0})])

    engine.advance()
    following = engine.writes

    assert [write.timestamp for write in following] == [FRAME_SIZE]


def test_schedule_validates_a_complete_batch_before_admitting_it():
    engine = AudioEngine()
    source = engine.source(Program(Sine))

    with pytest.raises(ValueError, match="has no register"):
        engine.schedule(
            [
                (0, source, {"frequency": 440.0}),
                (200, source, {"missing": True}),
            ]
        )

    assert engine.writes == ()


def test_fresh_graphs_render_deterministically():
    outputs = []
    for _ in range(2):
        engine = AudioEngine()
        oscillator = engine.source(Program(Sine))
        instrument = engine.composite(
            Program(lambda: Envelope(0.005, 0.002, 0.01, 0.4, 0.02))
        )
        instrument.connect(oscillator)
        engine.output = instrument
        engine.schedule(
            [
                (0, oscillator, {"frequency": 440.0}),
                (0, instrument, {"gate": True}),
                (1_200, instrument, {"gate": False}),
            ]
        )
        outputs.append(render(engine, 8))

    assert np.array_equal(outputs[0], outputs[1])


def test_register_constraints_reject_invalid_writes_before_rendering():
    with pytest.raises(ValueError, match="at least"):
        Delay(-1)
    with pytest.raises(ValueError, match="at most"):
        Delay(MAX_DELAY_TICKS + 1)
    with pytest.raises(TypeError, match="integer"):
        Delay(1.5)
    with pytest.raises(TypeError, match="integer"):
        Delay(True)
    with pytest.raises(TypeError, match="integer"):
        Crush(True)
    with pytest.raises(ValueError, match="at least"):
        Envelope(-1.0, 0.0, 0.0, 1.0, 0.0)
    with pytest.raises(ValueError, match="at most"):
        Envelope(0.0, 0.0, 0.0, 2.0, 0.0)

    engine = AudioEngine()
    instrument = engine.composite(
        Program(lambda: Envelope(0.1, 0.1, 0.1, 0.5, 0.1))
    )
    with pytest.raises(ValueError, match="at least"):
        engine.schedule([(0, instrument, {"attack": -1.0})])
    with pytest.raises(ValueError, match="at most"):
        engine.schedule([(0, instrument, {"sustain": 2.0})])

    echo = engine.composite(Program(lambda: Echo(5, max_delay_ticks=10)))
    with pytest.raises(ValueError, match="at most"):
        engine.schedule([(0, echo, {"delay_ticks": 11})])


def test_ahdsr_follows_attack_hold_decay_sustain_and_release():
    engine = AudioEngine()
    carrier = engine.source(Program(Constant))
    instrument = engine.composite(
        Program(lambda: Envelope(0.01, 0.005, 0.01, 0.25, 0.01))
    )
    instrument.connect(carrier)
    engine.output = instrument
    engine.schedule(
        [
            (0, instrument, {"gate": True}),
            (1_400, instrument, {"gate": False}),
        ]
    )

    samples = render(engine, 5)

    assert samples[0] == pytest.approx(0.0)
    assert samples[479] == pytest.approx(479 / 480, abs=1e-5)
    assert np.allclose(samples[480:720], 1.0)
    assert samples[720] == pytest.approx(1.0)
    assert samples[1_199] == pytest.approx(0.25 + 0.75 / 480, abs=1e-5)
    assert samples[1_200] == pytest.approx(0.25)
    assert samples[1_400] == pytest.approx(0.25)
    assert 0.0 < samples[1_879] < 0.001
    assert np.all(samples[1_880:] == 0.0)


def test_quiescent_stage_sleeps_until_a_register_write_wakes_it():
    instances = []

    def counting_source():
        instance = CountingSource()
        instances.append(instance)
        return instance

    engine = AudioEngine()
    voice = engine.source(
        Program(
            counting_source,
            lambda: Envelope(0.0, 0.0, 0.0, 1.0, 0.0),
        )
    )
    engine.output = voice

    first = engine.advance().current.data.copy()
    second = engine.advance().current.data.copy()

    assert np.all(first == 0.0)
    assert np.all(second == 0.0)
    assert instances[0].calls == 1
    assert voice.quiescent

    engine.schedule([(0, voice, {"gate": True})])
    awake = engine.advance().current.data.copy()

    assert np.all(awake == 1.0)
    assert instances[0].calls == 2
    assert not voice.quiescent


def test_sleeping_oscillator_resumes_without_free_running_phase_catch_up():
    engine = AudioEngine()
    voice = engine.source(
        Program(
            Sine,
            lambda: Envelope(0.0, 0.0, 0.0, 1.0, 0.0),
        )
    )
    engine.output = voice
    frequency = 100.0
    engine.schedule([(0, voice, {"frequency": frequency})])

    engine.advance()
    assert voice.quiescent
    for _ in range(5):
        engine.advance()

    engine.schedule([(0, voice, {"gate": True})])
    awake = engine.advance().current.data.copy()

    stored_phase = FRAME_SIZE * 2.0 * np.pi * frequency / SAMPLE_RATE
    assert awake[0] == pytest.approx(np.sin(stored_phase), abs=2e-5)


def test_mixing_desk_pulls_only_the_voice_woken_for_this_frame():
    instances = []

    def counting_source():
        instance = CountingSource()
        instances.append(instance)
        return instance

    engine = AudioEngine()
    voices = [
        engine.source(
            Program(
                counting_source,
                lambda: Envelope(0.0, 0.0, 0.0, 1.0, 0.0),
            )
        )
        for _ in range(16)
    ]
    desk = engine.mixing_desk()
    for voice in voices:
        desk.connect(voice)
    engine.output = desk

    engine.advance()
    engine.advance()
    assert [instance.calls for instance in instances] == [1] * len(voices)

    engine.schedule([(0, voices[7], {"gate": True})])
    output = engine.advance().current.data

    assert instances[7].calls == 2
    assert sum(instance.calls for instance in instances) == len(voices) + 1
    assert np.allclose(output, 2**-0.5)


def test_delay_history_keeps_a_stage_awake_after_a_quiet_output_frame():
    engine = AudioEngine()
    voice = engine.source(Program(Pulse, lambda: Delay(450)))
    engine.output = voice
    engine.schedule([(0, voice, {"gate": True})])

    first = engine.advance().current.data.copy()
    second = engine.advance().current.data.copy()

    assert np.all(first == 0.0)
    assert not voice.quiescent
    assert np.any(second != 0.0)

    engine.advance()
    final = engine.advance().current.data.copy()
    assert np.all(final == 0.0)
    assert voice.quiescent


def test_resonator_history_rings_out_before_its_stage_sleeps():
    engine = AudioEngine()
    voice = engine.source(Program(Pulse, Resonator))
    engine.output = voice
    engine.schedule([(0, voice, {"gate": True})])

    peaks = []
    for _ in range(60):
        peaks.append(float(np.max(np.abs(engine.advance().current.data))))
        if voice.quiescent:
            break

    assert peaks[0] > 0.001
    assert peaks[1] > 0.001
    assert len(peaks) > 2
    assert voice.quiescent
    assert peaks[-1] == 0.0


def test_gate_down_during_release_restarts_attack_from_the_current_level():
    engine = AudioEngine()
    carrier = engine.source(Program(Constant))
    instrument = engine.composite(
        Program(lambda: Envelope(0.01, 0.0, 0.0, 1.0, 0.02))
    )
    instrument.connect(carrier)
    engine.output = instrument
    engine.schedule(
        [
            (0, instrument, {"gate": True}),
            (600, instrument, {"gate": False}),
            (800, instrument, {"gate": True}),
        ]
    )

    samples = render(engine, 3)

    level_at_retrigger = 1.0 - 200 / 960
    assert samples[800] == pytest.approx(level_at_retrigger, abs=1e-5)
    assert samples[899] < 1.0
    assert samples[899] > 0.99
    assert samples[900] == pytest.approx(1.0)


def test_shared_source_is_processed_once_per_engine_frame():
    instances = []

    def counting_source():
        instance = CountingSource()
        instances.append(instance)
        return instance

    engine = AudioEngine()
    source = engine.source(Program(counting_source))
    left = engine.composite()
    right = engine.composite()
    output = engine.composite()
    left.connect(source)
    right.connect(source)
    output.connect(left)
    output.connect(right)
    engine.output = output

    samples = engine.advance().current.data

    assert len(instances) == 1
    assert instances[0].calls == 1
    assert np.all(samples == 2.0)


def test_stage_register_bank_is_shared_by_all_connection_programs():
    engine = AudioEngine()
    first = engine.source(Program(Constant))
    second = engine.source(Program(Constant))
    instrument = engine.composite(
        Program(lambda: Envelope(0.0, 0.0, 0.0, 1.0, 0.0))
    )
    instrument.connect(first)
    instrument.connect(second)
    engine.output = instrument
    engine.schedule([(FRAME_SIZE, instrument, {"gate": True})])

    silent = engine.advance().current.data.copy()
    sounding = engine.advance().current.data.copy()

    assert np.all(silent == 0.0)
    assert np.all(sounding == 2.0)


def test_input_registers_overlay_stage_registers_for_one_connection():
    engine = AudioEngine()
    first = engine.source(Program(Constant))
    second = engine.source(Program(Constant))
    instrument = engine.composite(
        Program(lambda: Envelope(0.0, 0.0, 0.0, 1.0, 0.0))
    )
    instrument.connect(first)
    instrument.connect(second)
    engine.output = instrument

    assert "gate" in instrument.registers
    assert "level" not in instrument.registers
    assert instrument.registers_for(first)["gate"] is False
    assert instrument.registers_for(first)["level"] == 1.0
    with pytest.raises(TypeError):
        instrument.registers_for(first)["level"] = 0.5

    engine.schedule(
        [
            (0, instrument, {"gate": True}),
            (0, instrument, first, {"gate": False, "level": 0.25}),
            (0, instrument, second, {"level": 0.75}),
        ]
    )
    samples = engine.advance().current.data.copy()

    assert np.all(samples == 0.75)
    assert instrument.registers["gate"] is True
    assert instrument.registers_for(first)["gate"] is False
    assert instrument.registers_for(first)["level"] == 0.25
    assert instrument.registers_for(second)["gate"] is True
    assert instrument.registers_for(second)["level"] == 0.75

    engine.schedule(
        [
            (0, instrument, {"gate": False}),
            (200, instrument, {"gate": True}),
        ]
    )
    samples = engine.advance().current.data.copy()

    assert np.all(samples[:200] == 0.0)
    assert np.all(samples[200:] == 0.75)


def test_lane_scoped_program_registers_are_declared_per_connection():
    engine = AudioEngine()
    source = engine.source(Program(Constant))
    stage = engine.composite(Program(LaneBias))
    stage.connect(source)
    engine.output = stage

    assert "bias" not in stage.registers
    assert stage.registers_for(source)["bias"] == 0.25
    assert np.all(engine.advance().current.data == 1.25)

    engine.schedule([(0, stage, source, {"bias": 0.5})])
    assert np.all(engine.advance().current.data == 1.5)

    with pytest.raises(ValueError, match="requires an input connection"):
        engine.source(Program(LaneBias))


def test_input_register_writes_require_the_exact_live_connection():
    engine = AudioEngine()
    source = engine.source(Program(Constant))
    unconnected = engine.source(Program(Constant))
    stage = engine.composite()
    stage.connect(source)

    with pytest.raises(ValueError, match="Stage has no register 'level'"):
        engine.schedule([(0, stage, {"level": 0.5})])
    with pytest.raises(ValueError, match="no input connected"):
        engine.schedule([(0, stage, unconnected, {"level": 0.5})])
    with pytest.raises(ValueError, match="Stage input has no register"):
        engine.schedule([(0, stage, source, {"missing": 0.5})])


def test_disconnect_discards_queued_lane_writes_before_a_clean_reconnect():
    engine = AudioEngine()
    source = engine.source(Program(Constant))
    stage = engine.composite()
    stage.connect(source)
    engine.output = stage
    engine.schedule([(FRAME_SIZE, stage, source, {"level": 0.0})])

    stage.disconnect(source)
    stage.connect(source)
    first = engine.advance().current.data.copy()
    second = engine.advance().current.data.copy()

    assert np.all(first == 1.0)
    assert np.all(second == 1.0)
    assert stage.registers_for(source)["level"] == 1.0


def test_fractional_frame_delay_keeps_only_its_own_lane_history():
    engine = AudioEngine()
    source = engine.source(Program(Ramp, lambda: Delay(450)))
    engine.output = source

    first = engine.advance().current.data.copy()
    second = engine.advance().current.data.copy()
    third = engine.advance().current.data.copy()

    assert np.array_equal(first, np.zeros(FRAME_SIZE))
    assert np.array_equal(
        second,
        np.concatenate((np.zeros(50), np.arange(350))).astype(np.float32),
    )
    assert np.array_equal(third, np.arange(350, 750, dtype=np.float32))


def test_feedback_reads_the_previous_settled_frame_with_one_frame_startup():
    engine = AudioEngine()
    source = engine.source(Program(Constant))
    feedback = engine.composite()
    echo = engine.composite()
    feedback.connect(source)
    feedback.connect(echo)
    echo.connect(feedback)
    engine.output = feedback

    first = engine.advance().current.data.copy()
    second = engine.advance().current.data.copy()
    third = engine.advance().current.data.copy()

    assert np.all(first == 1.0)
    assert np.all(second == 2.0)
    assert np.all(third == 3.0)


def test_quiescence_check_keeps_a_silent_feedback_cycle_awake_without_recursing():
    engine = AudioEngine()
    left = engine.composite()
    right = engine.composite()
    left.connect(right)
    right.connect(left)
    engine.output = left

    engine.advance()
    output = engine.advance().current.data

    assert np.all(output == 0.0)
    assert not left.quiescent


def test_mix_uses_the_first_lane_frame_as_its_destructive_accumulator():
    pool = FramePool()
    output = Bus(pool)
    first = Bus(pool)
    second = Bus(pool)
    output_id = output.current.id
    first_id = first.current.id
    first.current.data.fill(2.0)
    second.current.data.fill(3.0)

    result = Mix(output).process(first, second)

    assert result is output
    assert output.current.id == first_id
    assert first.current.id == output_id
    assert np.all(output.current.data == 5.0)


def test_frame_pool_ids_survive_growth_and_reuse_released_rows():
    pool = FramePool()
    first = Frame(pool)
    first.data.fill(7.0)
    followers = [Frame(pool) for _ in range(8)]

    assert first.id == 0
    assert np.all(first.data == 7.0)
    released_id = followers[3].id
    followers[3].release()
    replacement = Frame(pool)
    assert replacement.id == released_id


def test_empty_source_is_silent_and_cannot_accept_connections():
    engine = AudioEngine()
    source = engine.source()
    engine.output = source

    assert not hasattr(source, "connect")
    assert np.all(engine.advance().current.data == 0.0)


def test_pcm_sound_register_changes_and_gate_edges_are_sample_accurate():
    first = SoundSpec("first", np.full(800, 0.25), SAMPLE_RATE)
    second = SoundSpec("second", np.full(800, -0.5), SAMPLE_RATE)
    engine = AudioEngine()
    source = engine.source(Program(lambda: PCM(first)))
    engine.output = source
    engine.schedule(
        [
            (0, source, {"gate": True}),
            (150, source, {"sound": second}),
            (300, source, {"gate": False}),
        ]
    )

    samples = engine.advance().current.data.copy()

    assert np.all(samples[:150] == 0.25)
    assert np.all(samples[150:300] == -0.5)
    assert np.all(samples[300:] == 0.0)
    assert source.registers["sound"] is second


def test_pcm_speed_write_changes_sample_rate_without_resetting_position():
    sound = SoundSpec("ramp", np.arange(1_000) / 1_000, SAMPLE_RATE)
    engine = AudioEngine()
    source = engine.source(Program(lambda: PCM(sound)))
    engine.output = source
    engine.schedule(
        [
            (0, source, {"gate": True}),
            (200, source, {"speed": 2.0}),
        ]
    )

    samples = engine.advance().current.data.copy()

    expected_positions = np.concatenate(
        (np.arange(200), 200 + np.arange(200) * 2)
    )
    assert np.allclose(samples, expected_positions / 1_000, atol=1e-6)
    with pytest.raises(ValueError, match="at least"):
        engine.schedule([(FRAME_SIZE, source, {"speed": -1.0})])


def test_pcm_plays_a_resource_sound_at_its_native_rate(tmp_path):
    path = tmp_path / "sample.wav"
    source_samples = np.linspace(-0.75, 0.75, 120)
    encoded = np.rint(source_samples * 32_767).astype("<i2")
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(24_000)
        wav.writeframes(encoded.tobytes())

    resources = ResourceManager()
    sound = resources.sound(str(path))
    try:
        assert resources.sound(str(path)) is sound
        assert not sound.data.flags.writeable

        engine = AudioEngine()
        source = engine.source(Program(lambda: PCM(sound)))
        engine.output = source
        engine.schedule([(100, source, {"gate": True})])

        actual = engine.advance().current.data.copy()

        expected = np.zeros(FRAME_SIZE, dtype=np.float32)
        positions = np.arange(FRAME_SIZE - 100) * (sound.sr / SAMPLE_RATE)
        valid = positions <= len(sound.data) - 1
        expected[100:][valid] = np.interp(
            positions[valid],
            np.arange(len(sound.data)),
            sound.data,
        )
        assert np.allclose(actual, expected, atol=1e-6)
    finally:
        resources.remove(str(path))


def test_saw_is_a_continuous_frequency_controlled_source():
    engine = AudioEngine()
    source = engine.source(Program(Saw))
    engine.output = source
    engine.schedule([(0, source, {"frequency": SAMPLE_RATE / 8})])

    samples = engine.advance().current.data.copy()

    expected = np.tile(
        (-1.0, -0.75, -0.5, -0.25, 0.0, 0.25, 0.5, 0.75),
        50,
    )
    assert np.array_equal(samples, expected)


def test_metal_contains_its_fixed_inharmonic_frequency_bank():
    engine = AudioEngine()
    frequencies = (211.0, 307.0, 509.0)
    source = engine.source(Program(lambda: Metal(frequencies)))
    engine.output = source

    samples = render(engine, SAMPLE_RATE // FRAME_SIZE)
    spectrum = np.abs(np.fft.rfft(samples * np.hanning(len(samples))))
    bins = np.fft.rfftfreq(len(samples), 1.0 / SAMPLE_RATE)

    for frequency in frequencies:
        peak = spectrum[np.argmin(np.abs(bins - frequency))]
        skirts = spectrum[
            (bins >= frequency - 10.0) & (bins <= frequency + 10.0)
        ]
        assert peak == np.max(skirts)
        assert peak > 100.0


def test_metal_tune_is_sample_accurate_and_bank_is_not_a_register():
    engine = AudioEngine()
    source = engine.source(
        Program(lambda: Metal((SAMPLE_RATE / 8,), duty=0.5))
    )
    engine.output = source
    engine.schedule([(200, source, {"tune": 2.0})])

    samples = engine.advance().current.data.copy()
    expected = np.concatenate(
        (
            np.tile((1.0, 1.0, 1.0, 1.0, -1.0, -1.0, -1.0, -1.0), 25),
            np.tile((1.0, 1.0, -1.0, -1.0), 50),
        )
    )

    assert np.array_equal(samples, expected)
    with pytest.raises(ValueError, match="non-empty"):
        Metal(())
    with pytest.raises(ValueError, match="finite and positive"):
        Metal((220.0, -330.0))
    with pytest.raises(ValueError, match="has no register"):
        engine.schedule([(0, source, {"frequencies": (440.0,)})])


def test_pluck_is_pitchable_gate_triggered_and_deterministic():
    outputs = []
    for _ in range(2):
        engine = AudioEngine()
        source = engine.source(
            Program(lambda: Pluck(220.0, string_decay=2.0, damping=0.35))
        )
        engine.output = source
        engine.schedule(
            [
                (100, source, {"gate": True}),
                (SAMPLE_RATE // 2, source, {"frequency": 330.0}),
            ]
        )
        outputs.append(render(engine, SAMPLE_RATE // FRAME_SIZE))

    first, second = outputs
    assert np.array_equal(first, second)
    assert np.all(first[:100] == 0.0)
    assert np.any(first[100:FRAME_SIZE] != 0.0)

    def local_peak(samples, lower, upper):
        spectrum = np.abs(np.fft.rfft(samples * np.hanning(len(samples))))
        frequencies = np.fft.rfftfreq(len(samples), 1.0 / SAMPLE_RATE)
        band = (frequencies >= lower) & (frequencies <= upper)
        return frequencies[band][np.argmax(spectrum[band])]

    assert local_peak(first[7_200:21_600], 210.0, 230.0) == pytest.approx(
        220.0, abs=2.0
    )
    assert local_peak(first[31_200:45_600], 315.0, 345.0) == pytest.approx(
        330.0, abs=2.0
    )


def test_pluck_rejects_unprepared_or_invalid_live_controls():
    with pytest.raises(ValueError, match="at least"):
        Pluck(10.0, min_frequency=20.0)
    with pytest.raises(ValueError, match="min_frequency"):
        Pluck(min_frequency=0.0)
    with pytest.raises(TypeError, match="seed"):
        Pluck(seed=True)

    engine = AudioEngine()
    source = engine.source(Program(Pluck))
    with pytest.raises(ValueError, match="at least"):
        engine.schedule([(0, source, {"string_decay": 0.0})])
    with pytest.raises(ValueError, match="at most"):
        engine.schedule([(0, source, {"damping": 2.0})])

    voiced = engine.source(
        Program(
            Pluck,
            lambda: Envelope(0.0, 0.0, 0.0, 1.0, 0.0),
        )
    )
    assert "string_decay" in voiced.registers
    assert "decay" in voiced.registers


def test_pluck_retires_an_excitation_after_its_tail_is_inaudible():
    engine = AudioEngine()
    source = engine.source(
        Program(lambda: Pluck(220.0, string_decay=0.05, damping=0.8))
    )
    engine.output = source
    engine.schedule([(0, source, {"gate": True})])

    samples = render(engine, 2 * SAMPLE_RATE // FRAME_SIZE)

    assert np.any(samples[:FRAME_SIZE] != 0.0)
    assert np.all(samples[-FRAME_SIZE:] == 0.0)


def test_stereo_routing_and_pan():
    engine = AudioEngine()
    left = engine.source(Program(lambda: Constant(0.25)))
    right = engine.source(Program(lambda: Constant(0.5)))
    desk = engine.mixing_desk()
    desk.connect(left)
    desk.connect(right)
    engine.output = desk
    engine.schedule(
        [
            (0, desk, left, {"pan": -1.0}),
            (0, desk, right, {"pan": 1.0}),
        ]
    )

    first = engine.advance().current.data.copy()

    assert first.shape == (FRAME_SIZE, 2)
    assert np.all(first[:, 0] == 0.25)
    assert np.all(first[:, 1] == 0.5)
    assert desk.registers == {}
    assert desk.registers_for(left)["level"] == 1.0
    assert desk.registers_for(left)["pan"] == -1.0

    engine.schedule(
        [
            (0, desk, right, {"level": 0.2}),
            (200, desk, left, {"pan": 1.0}),
        ]
    )
    second = engine.advance().current.data.copy()

    assert np.all(second[:200, 0] == 0.25)
    assert np.allclose(second[:200, 1], 0.1)
    assert np.all(second[200:, 0] == 0.0)
    assert np.allclose(second[200:, 1], 0.35)


def test_mixing_desk_uses_constant_power_centre_pan():
    engine = AudioEngine()
    source = engine.source(Program(Constant))
    desk = engine.mixing_desk()
    desk.connect(source)
    engine.output = desk

    samples = engine.advance().current.data

    assert np.allclose(samples[:, 0], np.sqrt(0.5))
    assert np.allclose(samples[:, 1], np.sqrt(0.5))
    with pytest.raises(ValueError, match="at most"):
        engine.schedule([(0, desk, source, {"pan": 1.01})])


def test_stereo_mixing_desk_cannot_feed_a_mono_stage():
    engine = AudioEngine()
    mono = engine.composite()
    desk = engine.mixing_desk()

    with pytest.raises(ValueError, match="stereo Stage"):
        mono.connect(desk)


def test_register_modulation_by_control_signal():
    engine = AudioEngine()
    control = engine.source(Program(UnitRamp))
    target = engine.source(Program(Constant, lambda: Gain(0.25)))
    target.connect_signal(control, to="level")
    engine.output = target
    engine.schedule([(0, target, {"level": 0.75})])

    assert target.register_specs["level"].accepts_signal

    controlled = engine.advance().current.data.copy()

    assert np.allclose(
        controlled,
        np.linspace(0.0, 1.0, FRAME_SIZE, dtype=np.float32),
    )
    assert target.registers["level"] == 0.75

    target.disconnect_signal(control, to="level")
    fallback = engine.advance().current.data.copy()

    assert np.all(fallback == 0.75)


def test_register_signal_can_target_one_input_lane():
    engine = AudioEngine()
    signal = engine.source(Program(UnitRamp))
    source = engine.source(Program(Constant))
    desk = engine.mixing_desk()
    desk.connect(source)
    desk.connect_signal(signal, to="level", lane=source)
    engine.output = desk

    samples = engine.advance().current.data
    expected = (
        np.linspace(0.0, 1.0, FRAME_SIZE, dtype=np.float32)
        * np.sqrt(0.5)
    )

    assert np.allclose(samples[:, 0], expected)
    assert np.allclose(samples[:, 1], expected)
    assert desk.registers_for(source)["level"] == 1.0
    assert desk.register_specs_for(source)["level"].accepts_signal


def test_frequency_register_accepts_a_sample_rate_signal():
    engine = AudioEngine()
    control = engine.source(Program(FrequencySweep))
    oscillator = engine.source(Program(Sine))
    oscillator.connect_signal(control, to="frequency")
    engine.output = oscillator

    first = engine.advance().current.data.copy()
    second = engine.advance().current.data.copy()
    frequencies = np.linspace(
        220.0,
        880.0,
        FRAME_SIZE,
        dtype=np.float32,
    )
    phases = np.zeros(FRAME_SIZE, dtype=np.float64)
    phases[1:] = np.cumsum(frequencies[:-1], dtype=np.float64)
    phases *= 2.0 * np.pi / SAMPLE_RATE

    phase_advance = (
        frequencies.sum(dtype=np.float64) * 2.0 * np.pi / SAMPLE_RATE
    )

    assert np.allclose(first, np.sin(phases), atol=2e-5)
    assert np.allclose(second, np.sin(phases + phase_advance), atol=3e-5)


def test_one_signal_source_can_fan_out_to_multiple_register_inputs():
    instances = []

    def counting_signal():
        instance = CountingSource()
        instances.append(instance)
        return instance

    engine = AudioEngine()
    signal = engine.source(Program(counting_signal))
    source = engine.source(Program(Constant, lambda: Gain(0.25)))
    source.connect_signal(signal, to="level")
    desk = engine.mixing_desk()
    desk.connect(source)
    desk.connect_signal(signal, to="pan", lane=source)
    engine.output = desk

    samples = engine.advance().current.data

    assert len(instances) == 1
    assert instances[0].calls == 1
    assert np.allclose(samples[:, 0], 0.0, atol=1e-7)
    assert np.all(samples[:, 1] == 1.0)


def test_register_spec_rejects_incompatible_signal_connections():
    with pytest.raises(TypeError, match="real-valued default"):
        RegisterSpec(False, accepts_signal=True)

    engine = AudioEngine()
    signal = engine.source(Program(UnitRamp))
    enveloped = engine.source(
        Program(
            Constant,
            lambda: Envelope(0.0, 0.0, 0.0, 1.0, 0.0),
        )
    )
    with pytest.raises(ValueError, match="does not accept a signal"):
        enveloped.connect_signal(signal, to="gate")

    gained = engine.source(Program(Constant, Gain))
    with pytest.raises(ValueError, match="has no register"):
        gained.connect_signal(signal, to="missing")
    with pytest.raises(ValueError, match="mono signal"):
        gained.connect_signal(engine.mixing_desk(), to="level")

    foreign_signal = AudioEngine().source(Program(UnitRamp))
    with pytest.raises(ValueError, match="another AudioEngine"):
        gained.connect_signal(foreign_signal, to="level")


def test_offline_render_and_wav_export_complete_engine_frames(tmp_path):
    engine = AudioEngine()
    engine.output = engine.source(Program(lambda: Constant(2.0)))
    path = tmp_path / "render.wav"

    samples = render_wav(engine, path, 0.01)

    assert samples.shape == (2 * FRAME_SIZE,)
    assert samples.dtype == np.float32
    assert np.all(samples == 2.0)
    assert engine.clock == 2 * FRAME_SIZE
    with wave.open(str(path), "rb") as wav:
        assert wav.getnchannels() == 1
        assert wav.getsampwidth() == 2
        assert wav.getframerate() == SAMPLE_RATE
        assert wav.getnframes() == 2 * FRAME_SIZE
        encoded = np.frombuffer(wav.readframes(wav.getnframes()), dtype="<i2")
    assert np.all(encoded == 32_767)


def test_offline_render_and_wav_export_preserve_stereo(tmp_path):
    engine = AudioEngine()
    left = engine.source(Program(lambda: Constant(0.25)))
    right = engine.source(Program(lambda: Constant(0.5)))
    desk = engine.mixing_desk()
    desk.connect(left)
    desk.connect(right)
    engine.output = desk
    engine.schedule(
        [
            (0, desk, left, {"pan": -1.0}),
            (0, desk, right, {"pan": 1.0}),
        ]
    )
    path = tmp_path / "render-stereo.wav"

    samples = render_wav(engine, path, 0.01)

    assert samples.shape == (2 * FRAME_SIZE, 2)
    assert np.all(samples[:, 0] == 0.25)
    assert np.all(samples[:, 1] == 0.5)
    with wave.open(str(path), "rb") as wav:
        assert wav.getnchannels() == 2
        assert wav.getnframes() == 2 * FRAME_SIZE
        encoded = np.frombuffer(wav.readframes(wav.getnframes()), dtype="<i2")
    encoded = encoded.reshape(-1, 2)
    assert np.all(encoded[:, 0] == int(0.25 * 32_767))
    assert np.all(encoded[:, 1] == int(0.5 * 32_767))


def test_instrument_voice_allocation_and_percussion():
    engine = AudioEngine()
    voices = [kick(engine), snare(engine), clap(engine), hat(engine)]
    desk = engine.mixing_desk()
    events = []
    for index, voice in enumerate(voices):
        desk.connect(voice)
        timestamp = index * 2_000
        events.extend(
            [
                (timestamp, voice, {"gate": False, "level": 0.5}),
                (timestamp, voice, {"gate": True}),
            ]
        )
    engine.output = desk
    engine.schedule(events)

    samples = render(engine, 30)

    assert len({id(voice) for voice in voices}) == 4
    assert all("gate" in voice.register_specs for voice in voices)
    assert all("level" in voice.register_specs for voice in voices)
    assert samples.shape == (30 * FRAME_SIZE, 2)
    assert np.all(np.isfinite(samples))
    for index in range(4):
        start = index * 2_000
        assert np.max(np.abs(samples[start : start + 2_000])) > 0.001


@pytest.mark.parametrize(
    "factory",
    (
        drum,
        kick,
        noise_drum,
        snare,
        clap,
        shaker,
        metal_drum,
        hat,
        cowbell,
        cymbal,
    ),
)
def test_percussion_roots_and_tuned_presets_are_audible(factory):
    engine = AudioEngine()
    voice = factory(engine)
    engine.output = voice
    engine.schedule(
        [
            (0, voice, {"gate": False}),
            (0, voice, {"gate": True}),
        ]
    )

    samples = render(engine, 8)

    assert np.all(np.isfinite(samples))
    assert np.max(np.abs(samples)) > 0.001


def test_pulse_is_sample_accurate_and_retriggers_from_ordered_gate_edges():
    engine = AudioEngine()
    voice = engine.source(Program(Pulse))
    engine.output = voice
    engine.schedule([(37, voice, {"gate": True})])

    first = engine.advance().current.data.copy()

    assert np.all(first[:37] == 0.0)
    assert np.all(first[37:85] == 1.0)
    assert np.all(first[85:] == 0.0)

    engine.schedule(
        [
            (50, voice, {"gate": False}),
            (50, voice, {"gate": True}),
        ]
    )
    second = engine.advance().current.data.copy()

    assert np.all(second[:50] == 0.0)
    assert np.all(second[50:98] == 1.0)
    assert np.all(second[98:] == 0.0)


def test_resonator_turns_a_short_pulse_into_a_decaying_body():
    engine = AudioEngine()
    voice = engine.source(Program(Pulse, Resonator))
    engine.output = voice
    engine.schedule([(0, voice, {"gate": True})])

    first = engine.advance().current.data.copy()
    second = engine.advance().current.data.copy()

    assert np.max(np.abs(first)) > 0.001
    assert np.max(np.abs(second)) > 0.001
    assert np.max(np.abs(second)) < np.max(np.abs(first))
