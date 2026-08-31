from dataclasses import dataclass

import pytest

from blitspersecond.common import fps
from blitspersecond.input.buffer import InputBuffer


@dataclass(frozen=True)
class SourceFrame:
    frame: int
    value: str


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
def test_capacity_is_never_less_than_half_an_authored_second(
    rate: fps, capacity: int
):
    assert InputBuffer[SourceFrame](rate).capacity == capacity


def test_a_source_reads_without_draining_other_readers():
    history = InputBuffer[SourceFrame](fps.FIFTEEN)
    history.append(SourceFrame(4, "first"))
    history.append(SourceFrame(5, "second"))

    assert list(history.after(None)) == [
        SourceFrame(4, "first"),
        SourceFrame(5, "second"),
    ]
    assert list(history.after(4)) == [SourceFrame(5, "second")]
    assert list(history.after(None))[0] == SourceFrame(4, "first")


def test_an_expired_cursor_is_never_silently_replayed_partially():
    history = InputBuffer[SourceFrame](fps.FIFTEEN)
    for frame in range(9):
        history.append(SourceFrame(frame, str(frame)))

    assert [item.frame for item in history.after(0)] == list(range(1, 9))
    with pytest.raises(ValueError, match="expired"):
        list(history.after(-1))


def test_source_frames_must_be_sequential():
    history = InputBuffer[SourceFrame](fps.SIXTY)
    history.append(SourceFrame(2, "two"))

    with pytest.raises(ValueError, match="sequence"):
        history.append(SourceFrame(4, "four"))
