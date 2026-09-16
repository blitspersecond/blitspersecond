"""`Operator` and `Compositor`: the base, and the arity split.

**The arity split is a contract, not an accident**, and it is the one thing
here a future tidy-up could quietly undo. `Operator.process` is unary and
positional-only; `Compositor` widens it to N-ary. Collapsing them -- giving
the base `*inputs` so the two "match" -- is exactly what a 2026-08-14 review
recommended and a 2026-09-14 audit found already unnecessary. These tests
assert the split still holds, including that pyright's view of it
(no incompatible override) is backed by runtime behaviour.

`_tail_quiescent` is tested through a stub with its own state array rather
than through a real filter. The behaviour being pinned is the RULE -- how
long quiet must persist, and that the state is zeroed and not merely reported
-- which a real filter would obscure with its own arithmetic.
"""

import inspect

import numpy as np
import pytest

from blitspersecond.audio.synth.block import BlockPool
from blitspersecond.audio.synth.bus import Bus
from blitspersecond.audio.constants import BLOCK_SIZE
from blitspersecond.audio.synth.operators.operator import (
    _QUIET_BLOCKS,
    _SILENCE_FLOOR,
    Compositor,
    Operator,
)
from blitspersecond.audio.synth.registers import RegisterScope


@pytest.fixture
def bus() -> Bus:
    return Bus(BlockPool())


# -- the plain base ----------------------------------------------------


def test_the_base_passes_its_lane_through_untouched(bus):
    bus.current.data.fill(2.0)
    assert Operator().process(bus) is bus
    assert np.all(bus.current.data == 2.0)


def test_an_operator_declares_no_registers_and_stage_scope():
    assert Operator.registers == {}
    assert Operator.scope is RegisterScope.STAGE


def test_the_shared_register_mapping_cannot_be_written_through():
    """Every operator without its own registers shares this one object."""
    with pytest.raises(TypeError):
        Operator.registers["level"] = None  # type: ignore[index]


def test_control_accepts_a_block_and_does_nothing(bus):
    Operator().control(0, {}, ())


def test_an_unknown_operator_stays_awake(bus):
    """The default must never be to skip: silence is not proof of settling."""
    bus.current.data.fill(0.0)
    assert Operator()._output_quiescent(bus, True) is False


# -- the arity split ---------------------------------------------------


def test_the_base_is_unary_and_positional_only():
    parameters = list(inspect.signature(Operator.process).parameters.values())
    names = [p.name for p in parameters]
    assert names == ["self", "bus"]
    assert parameters[1].kind is inspect.Parameter.POSITIONAL_ONLY
    assert not any(p.kind is inspect.Parameter.VAR_POSITIONAL for p in parameters)


def test_a_compositor_is_the_n_ary_one():
    parameters = list(inspect.signature(Compositor.process).parameters.values())
    assert parameters[1].kind is inspect.Parameter.VAR_POSITIONAL


def test_a_compositor_is_an_operator():
    """Widening a positional-only unary base, which is why it type-checks."""
    assert issubclass(Compositor, Operator)


def test_a_bare_compositor_is_the_identity_on_one_lane(bus):
    bus.current.data.fill(3.0)
    assert Compositor().process(bus) is bus


def test_a_bare_compositor_refuses_more_than_one_lane(bus):
    """A subclass that forgets to override is a passthrough, not a reducer."""
    other = Bus(BlockPool())
    with pytest.raises(ValueError):
        Compositor().process(bus, other)
    with pytest.raises(ValueError):
        Compositor().process()


def test_the_refusal_names_the_subclass_that_failed(bus):
    class Blend(Compositor):
        pass

    with pytest.raises(ValueError, match="Blend"):
        Blend().process(bus, bus)


# -- retiring a tail ---------------------------------------------------


class _Ringing(Operator):
    """An operator with one state array, standing in for a filter."""

    def __init__(self, level: float):
        self.state = np.full(4, level, dtype=np.float32)

    def settle(self, bus: Bus, input_quiescent: bool = True) -> bool:
        return self._tail_quiescent(bus, input_quiescent, self.state)


def test_a_live_input_never_retires_a_tail(bus):
    operator = _Ringing(0.0)
    for _ in range(_QUIET_BLOCKS * 2):
        assert operator.settle(bus, input_quiescent=False) is False


def test_quiet_must_persist_before_the_tail_is_retired(bus):
    operator = _Ringing(0.0)
    for _ in range(_QUIET_BLOCKS - 1):
        assert operator.settle(bus) is False
    assert operator.settle(bus) is True


def test_audible_state_keeps_it_awake_even_on_a_silent_output(bus):
    """The output can be silent while the state is still ringing."""
    operator = _Ringing(_SILENCE_FLOOR * 10)
    for _ in range(_QUIET_BLOCKS * 2):
        assert operator.settle(bus) is False


def test_one_loud_block_restarts_the_count(bus):
    operator = _Ringing(0.0)
    for _ in range(_QUIET_BLOCKS - 1):
        operator.settle(bus)
    bus.current.data.fill(1.0)
    assert operator.settle(bus) is False
    bus.current.data.fill(0.0)

    for _ in range(_QUIET_BLOCKS - 1):
        assert operator.settle(bus) is False
    assert operator.settle(bus) is True


def test_retiring_zeroes_the_state_rather_than_just_reporting_it(bus):
    """So the next wake starts from silence, not a tail nobody heard."""
    operator = _Ringing(_SILENCE_FLOOR / 100)
    bus.current.data.fill(_SILENCE_FLOOR / 100)

    for _ in range(_QUIET_BLOCKS - 1):
        operator.settle(bus)
    assert operator.settle(bus) is True

    assert np.all(operator.state == 0.0)
    assert np.all(bus.current.data == 0.0)


def test_the_effective_floor_is_the_nearest_float32_above_it(bus):
    """The constant is float64; the samples it is compared against are not.

    `1e-4` has no float32 representation, and `float32(1e-4)` is
    9.99999975e-05 -- just BELOW the constant. So filling a state array with
    `_SILENCE_FLOOR` itself does not keep a tail alive, and the real threshold
    is the next float32 value up. Worth pinning rather than discovering: the
    obvious reading of the constant is off by one representable step.
    """
    audible = float(np.nextafter(np.float32(_SILENCE_FLOOR), np.float32(1.0)))
    assert audible >= _SILENCE_FLOOR
    ringing = _Ringing(audible)
    for _ in range(_QUIET_BLOCKS * 2):
        assert ringing.settle(bus) is False

    inaudible = float(np.float32(_SILENCE_FLOOR))
    assert inaudible < _SILENCE_FLOOR
    settling = _Ringing(inaudible)
    for _ in range(_QUIET_BLOCKS - 1):
        settling.settle(bus)
    assert settling.settle(bus) is True


def test_two_operators_count_their_own_quiet(bus):
    """The counter is a class default; it must not be shared once written."""
    first, second = _Ringing(0.0), _Ringing(0.0)
    for _ in range(_QUIET_BLOCKS - 1):
        first.settle(bus)
    assert second.settle(bus) is False
    assert first.settle(bus) is True


def test_an_operator_with_no_state_arrays_still_retires(bus):
    operator = Operator()
    for _ in range(_QUIET_BLOCKS - 1):
        assert operator._tail_quiescent(bus, True) is False
    assert operator._tail_quiescent(bus, True) is True


def test_an_empty_state_array_is_not_treated_as_audible(bus):
    operator = Operator()
    empty = np.zeros(0, dtype=np.float32)
    for _ in range(_QUIET_BLOCKS - 1):
        operator._tail_quiescent(bus, True, empty)
    assert operator._tail_quiescent(bus, True, empty) is True


def test_a_block_sized_state_array_is_measured_in_full(bus):
    """One loud sample anywhere in the state keeps the tail alive."""
    operator = Operator()
    state = np.zeros(BLOCK_SIZE, dtype=np.float32)
    state[-1] = 1.0
    for _ in range(_QUIET_BLOCKS * 2):
        assert operator._tail_quiescent(bus, True, state) is False
