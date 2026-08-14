"""Deterministic main-loop tests at the presentation-policy boundary.

No window, GL context, real clock or sleep is involved. These fakes exercise
the machine loop and its display-owned PresentationSession together at the
boundary where fixed game ticks map onto presentation slots.
"""

from collections.abc import Iterable
from types import SimpleNamespace

import pytest

from blitspersecond import BlitsPerSecond
from blitspersecond import loop as loop_module
from blitspersecond.display.internal import PresentationSession
from blitspersecond.display.internal.presentation import (
    session as presentation_module,
)
from blitspersecond.lifecycle import EngineState
from blitspersecond.system.monitor.frame_pacing import (
    ENV_START_DEADLINE,
    FramePacingRecorder,
    start_deadline_from_environment,
)


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def perf_counter(self) -> float:
        return self.now

    def sleep(self, duration: float) -> None:
        assert duration >= 0
        self.now += duration


class FakeDisplay:
    def __init__(
        self,
        clock: FakeClock,
        refresh_rate: float,
        swap_duration: float | Iterable[float],
        outputs: tuple[float, ...] | None = None,
    ) -> None:
        self.clock = clock
        self.refresh_rate = refresh_rate
        self.outputs = (
            (refresh_rate,) if outputs is None else outputs
        )
        if isinstance(swap_duration, (int, float)):
            self._swap_durations = iter(())
            self._next_swap_duration = float(swap_duration)
        else:
            durations = iter(swap_duration)
            self._next_swap_duration = float(next(durations))
            self._swap_durations = durations
        self._swap_duration = 0.0
        self.closed = False
        self.prepared: list[bool] = []
        self.presentations: list[float] = []
        self.logger = None

    @property
    def swap_duration(self) -> float:
        return self._swap_duration

    def output_refresh_rates(self) -> tuple[float, ...]:
        """The outputs a measured presentation rate may be re-cadenced onto.

        Defaults to this display's own rate, so these fixtures describe a
        single-output machine unless a test says otherwise.
        """
        return self.outputs

    def dispatch_events(self) -> None:
        pass

    def _presentation_session(self) -> PresentationSession:
        assert self.logger is not None
        return PresentationSession(
            self,
            target_rate=60,
            spin_pacing=False,
            logger=self.logger,
            frame_pacing_factory=FramePacingRecorder.from_environment,
            diagnostic_deadline=start_deadline_from_environment(),
        )

    def prepare(self, recompose: bool = True) -> None:
        self.prepared.append(recompose)

    def present(self) -> None:
        self._swap_duration = self._next_swap_duration
        self.clock.now += self._swap_duration
        self.presentations.append(self.clock.now)
        self._next_swap_duration = float(
            next(self._swap_durations, self._next_swap_duration)
        )

    def close(self) -> None:
        self.closed = True


class FakePace:
    def __init__(self) -> None:
        self.samples: list[float] = []

    def push(self, sample: float) -> None:
        self.samples.append(sample)

    def rebase(self) -> None:
        pass

    def report(self) -> None:
        pass


class FakeAudioDriver:
    def __init__(self, clock: FakeClock) -> None:
        self.clock = clock
        self.start_delay = 0.0
        self.starts = 0
        self.closes = 0

    def start(self) -> None:
        self.starts += 1
        self.clock.now += self.start_delay

    def close(self) -> None:
        self.closes += 1


class FakeLogger:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def info(self, message: str) -> None:
        self.messages.append(message)

    def warning(self, message: str) -> None:
        self.messages.append(message)

    def error(self, message: str) -> None:
        raise AssertionError(message)


def loop_engine(
    monkeypatch: pytest.MonkeyPatch,
    *,
    refresh_rate: float,
    swap_duration: float | Iterable[float],
    start_deadline: bool = False,
    gamescope: bool = False,
    outputs: tuple[float, ...] | None = None,
) -> tuple[BlitsPerSecond, FakeClock, FakeDisplay]:
    clock = FakeClock()
    display = FakeDisplay(clock, refresh_rate, swap_duration, outputs)
    config = SimpleNamespace(
        display=SimpleNamespace(fps=60, spin_pacing=False),
    )

    monkeypatch.setattr(loop_module, "perf_counter", clock.perf_counter)
    monkeypatch.setattr(loop_module.clock, "tick", lambda: None)
    monkeypatch.setattr(
        presentation_module,
        "perf_counter",
        clock.perf_counter,
    )
    monkeypatch.setattr(presentation_module, "sleep", clock.sleep)
    if start_deadline:
        monkeypatch.setenv(ENV_START_DEADLINE, "1")
    else:
        monkeypatch.delenv(ENV_START_DEADLINE, raising=False)
    if gamescope:
        monkeypatch.setenv("GAMESCOPE_WAYLAND_DISPLAY", "gamescope-0")
        monkeypatch.setenv("XDG_CURRENT_DESKTOP", "gamescope")
        monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
        monkeypatch.setenv("BPS_GAMESCOPE_BACKEND", "wayland")
    else:
        monkeypatch.delenv("GAMESCOPE_WAYLAND_DISPLAY", raising=False)
        monkeypatch.delenv("BPS_GAMESCOPE_BACKEND", raising=False)

    engine = BlitsPerSecond.__new__(BlitsPerSecond)
    # Deliberately replace concrete runtime collaborators on this unbooted
    # instance. object.__setattr__ keeps the test's structural fakes from
    # pretending to be the production classes to the type checker.
    object.__setattr__(engine, "_config", config)
    logger = FakeLogger()
    display.logger = logger
    object.__setattr__(engine, "_logger", logger)
    object.__setattr__(engine, "_metrics", SimpleNamespace(pace=FakePace()))
    object.__setattr__(
        engine,
        "_events",
        SimpleNamespace(dispatch_event=lambda *_args: None),
    )
    object.__setattr__(engine, "_display", display)
    object.__setattr__(engine, "_audio_driver", FakeAudioDriver(clock))
    object.__setattr__(engine, "_kbm", SimpleNamespace(_update=lambda: None))
    object.__setattr__(engine, "_pads", SimpleNamespace(update=lambda: None))
    engine._tick = None
    engine._running = False
    engine._state = EngineState.STOPPED
    engine._user_suspended = False
    engine._backgrounded = False
    return engine, clock, display


def run_for_ticks(
    engine: BlitsPerSecond,
    clock: FakeClock,
    count: int,
) -> list[float]:
    ticks: list[float] = []

    def tick(current: BlitsPerSecond) -> None:
        ticks.append(clock.now)
        if len(ticks) == count:
            current.stop()

    engine.run(tick)
    return ticks


def test_blocking_vsync_path_keeps_one_tick_per_60hz_presentation(monkeypatch):
    period = 1.0 / 60
    engine, clock, _display = loop_engine(
        monkeypatch,
        refresh_rate=60,
        swap_duration=period,
    )

    ticks = run_for_ticks(engine, clock, 3)

    assert ticks == pytest.approx([0.0, period, period * 2])


def test_audio_readiness_precedes_first_presentation_timing(monkeypatch):
    period = 1.0 / 60
    engine, clock, _display = loop_engine(
        monkeypatch,
        refresh_rate=60,
        swap_duration=period,
    )
    driver = engine._audio_driver
    assert isinstance(driver, FakeAudioDriver)
    driver.start_delay = 0.75

    ticks = run_for_ticks(engine, clock, 2)

    assert ticks == pytest.approx([0.75, 0.75 + period])
    assert driver.starts == 1
    assert driver.closes == 1


def test_audio_startup_failure_aborts_before_presentation(monkeypatch):
    engine, _clock, display = loop_engine(
        monkeypatch,
        refresh_rate=60,
        swap_duration=1.0 / 60,
    )
    driver = engine._audio_driver
    assert isinstance(driver, FakeAudioDriver)

    def fail():
        driver.starts += 1
        raise RuntimeError("no audio output")

    driver.start = fail

    with pytest.raises(RuntimeError, match="no audio output"):
        engine.run()

    assert engine.state is EngineState.STOPPED
    assert display.closed
    assert driver.starts == 1
    assert driver.closes == 1


def test_presentation_capture_backend_is_injected_after_metadata_is_known(
    monkeypatch,
):
    clock = FakeClock()
    display = FakeDisplay(clock, refresh_rate=120, swap_duration=1 / 120)
    logger = FakeLogger()
    metadata = {}

    monkeypatch.setattr(
        presentation_module,
        "perf_counter",
        clock.perf_counter,
    )

    def capture_factory(**values):
        metadata.update(values)
        return None

    PresentationSession(
        display,
        target_rate=60,
        spin_pacing=False,
        logger=logger,
        frame_pacing_factory=capture_factory,
        diagnostic_deadline=False,
    )

    assert metadata["target_fps"] == 60
    assert metadata["refresh_hz"] == 120
    assert metadata["cadence"] == "1/2"
    assert metadata["presentation_path"]


@pytest.mark.parametrize("refresh_rate", [60, 120])
def test_nonblocking_detection_holds_simulation_then_joins_deadline_grid(
    monkeypatch,
    refresh_rate,
):
    tick_period = 1.0 / 60
    engine, clock, display = loop_engine(
        monkeypatch,
        refresh_rate=refresh_rate,
        swap_duration=0.0001,
    )

    ticks = run_for_ticks(engine, clock, 3)

    # The first frame is simulated immediately. Fast probe swaps then re-present
    # that state; they must not run several game ticks at kHz. A held bridge
    # presentation establishes the deadline before simulation resumes.
    assert ticks[0] == 0.0
    assert ticks[1] - ticks[0] >= tick_period
    assert ticks[2] - ticks[1] == pytest.approx(tick_period, abs=0.0002)
    assert display.prepared[:6] == [True, False, False, False, False, False]


def test_single_fast_queue_slack_flip_holds_state_without_losing_cadence(
    monkeypatch,
):
    period = 1.0 / 120
    engine, clock, display = loop_engine(
        monkeypatch,
        refresh_rate=120,
        swap_duration=[0.0001, period],
    )

    ticks = run_for_ticks(engine, clock, 3)

    # The first fast flip is ambiguous, so the second loop presents the held
    # state. Once blocking is observed, the preserved 1/2 phase supplies the
    # other hold before the next tick.
    assert ticks == pytest.approx([0.0, period * 2 + 0.0001, period * 4 + 0.0001])
    assert display.prepared[:3] == [True, False, False]


def test_deadline_experiment_skips_flip_probe_from_first_frame(monkeypatch):
    period = 1.0 / 120
    engine, clock, display = loop_engine(
        monkeypatch,
        refresh_rate=120,
        swap_duration=0.0001,
        start_deadline=True,
    )

    ticks = run_for_ticks(engine, clock, 3)
    logger = engine._logger

    assert ticks == pytest.approx([0.0, period * 2 + 0.0001, period * 4 + 0.0001])
    assert display.prepared == [True, False, True, False]
    assert isinstance(logger, FakeLogger)
    assert any(
        "requested deadline-grid" in message
        for message in logger.messages
    )


def test_gamescope_path_skips_flip_probe_without_diagnostic_flag(monkeypatch):
    period = 1.0 / 120
    engine, clock, display = loop_engine(
        monkeypatch,
        refresh_rate=120,
        swap_duration=0.0001,
        gamescope=True,
    )

    ticks = run_for_ticks(engine, clock, 3)
    logger = engine._logger

    assert ticks == pytest.approx([0.0, period * 2 + 0.0001, period * 4 + 0.0001])
    assert display.prepared == [True, False, True, False]
    assert isinstance(logger, FakeLogger)
    assert any("Gamescope presentation path" in message for message in logger.messages)


def test_moving_to_a_slower_output_re_derives_the_cadence(monkeypatch):
    # Launch on 144Hz (cadence 5/12), then have presentations start arriving
    # at 60Hz, as they do when the window is dragged to a 60Hz monitor. With
    # a fixed cadence the simulation would settle at 60 * 5/12 = 25 fps.
    fast, slow = 1.0 / 144, 1.0 / 60
    engine, clock, _display = loop_engine(
        monkeypatch,
        refresh_rate=144,
        swap_duration=[fast] * 40 + [slow] * 2000,
        outputs=(144.0, 60.0),
    )

    ticks = run_for_ticks(engine, clock, 200)

    settled = ticks[-60:]
    intervals = [b - a for a, b in zip(settled, settled[1:])]
    assert sum(intervals) / len(intervals) == pytest.approx(1 / 60, rel=0.05)


def test_moving_to_a_faster_output_re_derives_the_cadence(monkeypatch):
    # The dangerous direction: a 1/1 cadence delivered at 144Hz runs the game
    # at 144 fps, 2.4x real time, until the rate is re-derived.
    fast, slow = 1.0 / 144, 1.0 / 60
    engine, clock, _display = loop_engine(
        monkeypatch,
        refresh_rate=60,
        swap_duration=[slow] * 40 + [fast] * 2000,
        outputs=(144.0, 60.0),
    )

    ticks = run_for_ticks(engine, clock, 200)

    settled = ticks[-60:]
    intervals = [b - a for a, b in zip(settled, settled[1:])]
    assert sum(intervals) / len(intervals) == pytest.approx(1 / 60, rel=0.05)


def test_re_derivation_is_reported(monkeypatch):
    fast, slow = 1.0 / 144, 1.0 / 60
    engine, clock, _display = loop_engine(
        monkeypatch,
        refresh_rate=144,
        swap_duration=[fast] * 40 + [slow] * 2000,
        outputs=(144.0, 60.0),
    )

    run_for_ticks(engine, clock, 200)
    logger = engine._logger

    assert isinstance(logger, FakeLogger)
    assert any(
        "Presentation rate changed" in message for message in logger.messages
    ), "a silent cadence change would be worse than the bug"


def test_a_slow_game_never_re_derives_the_cadence(monkeypatch):
    # Half-rate presentation on a 144Hz display is a steady 72Hz that no
    # output is running at. Re-cadencing onto it would silently convert a
    # performance problem into a "correctly paced" slower game.
    engine, clock, _display = loop_engine(
        monkeypatch,
        refresh_rate=144,
        swap_duration=2.0 / 144,
        outputs=(144.0, 60.0),
    )

    run_for_ticks(engine, clock, 200)
    logger = engine._logger

    assert isinstance(logger, FakeLogger)
    assert not any(
        "Presentation rate changed" in message for message in logger.messages
    )


def test_deadline_grid_path_never_re_derives_from_its_own_timer(monkeypatch):
    # Under Gamescope the interval between presentations is the engine's own
    # deadline, so it is not evidence about any display.
    engine, clock, _display = loop_engine(
        monkeypatch,
        refresh_rate=120,
        swap_duration=0.0001,
        gamescope=True,
        outputs=(120.0, 60.0),
    )

    run_for_ticks(engine, clock, 200)
    logger = engine._logger

    assert isinstance(logger, FakeLogger)
    assert not any(
        "Presentation rate changed" in message for message in logger.messages
    )
