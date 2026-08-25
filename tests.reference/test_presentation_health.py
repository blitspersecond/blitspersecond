"""Presentation-health classification policy."""

from blitspersecond.display.internal import FlipHealth, PresentationMonitor
from blitspersecond.system.events import EventBus


def test_visibility_policy_events_are_registered_on_the_engine_bus():
    assert "on_background" in EventBus.event_types
    assert "on_foreground" in EventBus.event_types


def test_presentation_monitor_reset_forgets_stall_and_fast_streaks():
    monitor = PresentationMonitor(
        fast_threshold=0.001,
        fast_streak=2,
        stall_threshold=0.25,
        stall_streak=2,
    )

    assert monitor.observe(0.5) is FlipHealth.OK
    monitor.reset()
    assert monitor.observe(0.5) is FlipHealth.OK
    assert monitor.observe(0.5) is FlipHealth.STALLED

    monitor.reset()
    assert monitor.observe(0.0) is FlipHealth.PROBING
    monitor.reset()
    assert monitor.observe(0.0) is FlipHealth.PROBING
    assert monitor.observe(0.0) is FlipHealth.UNTHROTTLED


def test_fast_flip_probe_is_explicit_and_unthrottled_verdict_is_sticky():
    monitor = PresentationMonitor(fast_streak=3)

    assert monitor.observe(0.0) is FlipHealth.PROBING
    assert monitor.observe(0.0) is FlipHealth.PROBING
    assert monitor.observe(0.0) is FlipHealth.UNTHROTTLED

    # Deadline pacing moves the wait before flip(), so an occasional longer
    # swap cannot prove that blocking vsync has returned.
    assert monitor.observe(0.002) is FlipHealth.UNTHROTTLED

    monitor.reset()
    assert monitor.observe(0.002) is FlipHealth.OK


def test_monitor_can_start_sticky_unthrottled_for_deadline_experiment():
    monitor = PresentationMonitor(initial_health=FlipHealth.UNTHROTTLED)

    assert monitor.observe(0.008) is FlipHealth.UNTHROTTLED
    monitor.reset()
    assert monitor.observe(0.008) is FlipHealth.UNTHROTTLED
