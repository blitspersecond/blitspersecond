"""The retention ring: how much it keeps, and how it hands it out."""

import pytest

from blitspersecond.common import fps
from blitspersecond.input.stream import (
    WINDOW_SECONDS,
    InputStream,
    InputWindow,
)


def _window(frame: int, scan: str = "") -> InputWindow[str, str]:
    return InputWindow(frame=frame, scan=scan, facts=())


@pytest.mark.parametrize(
    ("rate", "capacity"),
    [
        (fps.FIFTEEN, 8),
        (fps.TWENTY, 10),
        (fps.THIRTY, 15),
        (fps.FORTY, 20),
        (fps.SIXTY, 30),
    ],
)
def test_capacity_is_half_a_second_of_authored_time(rate, capacity):
    assert InputStream(rate).capacity == capacity
    assert capacity == -(-int(rate) * WINDOW_SECONDS // 1)


def test_the_ring_drops_its_oldest_window_and_keeps_the_rest():
    stream: InputStream[str, str] = InputStream(fps.FIFTEEN)
    for frame in range(12):
        stream.append(_window(frame))

    assert len(stream) == 8
    assert stream.newest is not None and stream.newest.frame == 11
    assert [w.frame for w in stream.recent(99)] == list(range(4, 12))


def test_recent_gives_the_last_n_oldest_first_and_cannot_be_starved():
    stream: InputStream[str, str] = InputStream(fps.SIXTY)
    for frame in range(5):
        stream.append(_window(frame))

    assert [w.frame for w in stream.recent(3)] == [2, 3, 4]
    assert [w.frame for w in stream.recent(50)] == [0, 1, 2, 3, 4]
    assert stream.recent(0) == ()


def test_a_cursor_older_than_the_ring_is_told_rather_than_part_answered():
    stream: InputStream[str, str] = InputStream(fps.FIFTEEN)
    for frame in range(12):
        stream.append(_window(frame))

    assert [w.frame for w in stream.after(9)] == [10, 11]
    with pytest.raises(ValueError, match="expired"):
        list(stream.after(0))


def test_windows_must_arrive_in_frame_sequence():
    stream: InputStream[str, str] = InputStream(fps.SIXTY)
    stream.append(_window(7))
    stream.append(_window(8))

    with pytest.raises(ValueError, match="sequence"):
        stream.append(_window(10))


def test_an_empty_stream_answers_rather_than_raising():
    stream: InputStream[str, str] = InputStream(fps.SIXTY)
    assert stream.newest is None
    assert stream.recent(5) == ()
    assert list(stream.after(None)) == []
    assert list(stream.after(3)) == []
