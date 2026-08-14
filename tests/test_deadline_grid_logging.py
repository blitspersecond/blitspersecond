from importlib import import_module

import pytest


presentation_module = import_module(
    "blitspersecond.display.internal.presentation.session"
)


class FakeLogger:
    def __init__(self):
        self.messages = []

    def info(self, message):
        self.messages.append(("info", message))

    def warning(self, message):
        self.messages.append(("warning", message))


@pytest.mark.parametrize(
    ("platform", "level", "phrase"),
    [
        ("win32", "info", "expected deadline-grid"),
        ("linux", "warning", "not blocking on vsync"),
    ],
)
def test_deadline_grid_transition_has_platform_appropriate_severity(
    monkeypatch,
    platform,
    level,
    phrase,
):
    logger = FakeLogger()
    monkeypatch.setattr(presentation_module.sys, "platform", platform)

    presentation_module._log_deadline_grid_pacing(logger)

    assert len(logger.messages) == 1
    actual_level, message = logger.messages[0]
    assert actual_level == level
    assert phrase in message


def test_gamescope_deadline_start_is_an_expected_information_message():
    logger = FakeLogger()

    presentation_module._log_deadline_grid_pacing(
        logger,
        initial_reason="gamescope",
    )

    assert logger.messages == [
        (
            "info",
            "Gamescope presentation path selected deadline-grid pacing "
            "from the first frame.",
        )
    ]
