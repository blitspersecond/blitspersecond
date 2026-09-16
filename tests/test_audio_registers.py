"""`RegisterSpec` and `RegisterWrite`: what may be written to a sound.

**The default is the type declaration**, so most of these tests are about
what a spec REFUSES rather than what it stores. The refusals are where the
value is: a register that quietly accepts the wrong kind of value produces a
wrong sound with no error attached to it, and audio has no equivalent of
looking at the screen to notice.

Two cases are easy to get wrong in ways a casual test would miss, and both
have their own tests here:

**`True` is an `int` in Python.** Without an explicit bool-first branch,
writing `True` to a frequency would be accepted and would set it to 1 Hz.

**Every comparison against NaN is False.** A bounds check spelled the
readable way round -- `value < minimum` -- admits NaN silently. The spelling
in `_validate` rejects it, and `test_a_bounded_register_rejects_nan` fails if
anyone tidies that into the obvious form.
"""

import pytest

from blitspersecond.audio.synth.registers import (
    RegisterScope,
    RegisterSpec,
    RegisterWrite,
)

NAN = float("nan")
INF = float("inf")


# -- the default declares the type -------------------------------------


def test_a_number_is_stored_as_the_default_s_type():
    spec = RegisterSpec(1.0)
    stored = spec.normalize("level", 2)
    assert stored == 2.0
    assert isinstance(stored, float)


def test_an_integer_register_refuses_a_float():
    spec = RegisterSpec(8, minimum=1, maximum=24)
    assert spec.normalize("bits", 12) == 12
    with pytest.raises(TypeError):
        spec.normalize("bits", 12.5)


def test_a_gate_takes_only_gates():
    spec = RegisterSpec(False)
    assert spec.normalize("gate", True) is True
    with pytest.raises(TypeError):
        spec.normalize("gate", 1)
    with pytest.raises(TypeError):
        spec.normalize("gate", 1.0)


def test_true_is_not_a_frequency():
    """`bool` subclasses `int`, so this is accepted as 1 Hz without the
    bool-first branch -- an audible wrong answer, not an error."""
    spec = RegisterSpec(440.0, minimum=0.0)
    with pytest.raises(TypeError):
        spec.normalize("frequency", True)


def test_a_structured_value_is_kept_as_itself():
    """PCM's `sound` holds a SoundSpec; nothing may rebuild it from a number."""

    class Sample:
        pass

    original = Sample()
    spec = RegisterSpec(original)

    assert spec.normalize("sample", original) is original
    with pytest.raises(TypeError):
        spec.normalize("sample", 3.0)


def test_a_string_is_never_a_number():
    spec = RegisterSpec(1.0)
    with pytest.raises(TypeError):
        spec.normalize("level", "1.0")


# -- bounds ------------------------------------------------------------


def test_bounds_are_inclusive():
    spec = RegisterSpec(0.5, minimum=0.0, maximum=1.0)
    assert spec.normalize("level", 0.0) == 0.0
    assert spec.normalize("level", 1.0) == 1.0


def test_out_of_range_writes_are_refused():
    spec = RegisterSpec(0.5, minimum=0.0, maximum=1.0)
    with pytest.raises(ValueError):
        spec.normalize("level", -0.001)
    with pytest.raises(ValueError):
        spec.normalize("level", 1.001)


def test_a_bounded_register_rejects_nan():
    """Fails if `_validate` is tidied into `value < minimum`."""
    spec = RegisterSpec(440.0, minimum=0.0, maximum=24_000.0)
    with pytest.raises(ValueError):
        spec.normalize("frequency", NAN)


def test_a_bounded_register_rejects_infinity():
    spec = RegisterSpec(440.0, minimum=0.0, maximum=24_000.0)
    with pytest.raises(ValueError):
        spec.normalize("frequency", INF)


@pytest.mark.parametrize("value", [NAN, INF, -INF])
def test_an_unbounded_register_admits_nan_and_infinity(value):
    """Current behaviour, pinned so a change has to be deliberate.

    An unbounded register has nothing to compare against, so the NaN rejection
    above does not reach it -- `Gain.level` is declared this way. See the
    board card; if a spec-wide finiteness check lands, this test is the one
    that must be rewritten to say so.
    """
    spec = RegisterSpec(1.0, unit="linear")
    assert spec.normalize("level", value) == pytest.approx(value, nan_ok=True)


# -- a spec that contradicts itself ------------------------------------


def test_a_default_outside_its_own_bounds_raises_where_it_is_written():
    with pytest.raises(ValueError):
        RegisterSpec(5.0, minimum=0.0, maximum=1.0)


def test_minimum_above_maximum_raises():
    with pytest.raises(ValueError):
        RegisterSpec(0.5, minimum=1.0, maximum=0.0)


def test_only_a_real_register_can_take_a_signal():
    """A gate cannot be a waveform, and neither can a SoundSpec."""
    RegisterSpec(1.0, accepts_signal=True)
    with pytest.raises(TypeError):
        RegisterSpec(False, accepts_signal=True)
    with pytest.raises(TypeError):
        RegisterSpec(object(), accepts_signal=True)


def test_accepts_signal_must_be_a_boolean():
    with pytest.raises(TypeError):
        RegisterSpec(1.0, accepts_signal=1)  # type: ignore[arg-type]


# -- specs compare by value --------------------------------------------


def test_identical_declarations_are_equal():
    """Two Programs on one Stage may declare the same register; agreement is
    tested by equality, so a spec must not compare by identity."""
    left = RegisterSpec(440.0, minimum=0.0, unit="Hz", accepts_signal=True)
    right = RegisterSpec(440.0, minimum=0.0, unit="Hz", accepts_signal=True)
    assert left == right


def test_specs_differing_in_any_field_are_unequal():
    base = RegisterSpec(440.0, minimum=0.0, unit="Hz")
    assert base != RegisterSpec(441.0, minimum=0.0, unit="Hz")
    assert base != RegisterSpec(440.0, minimum=1.0, unit="Hz")
    assert base != RegisterSpec(440.0, minimum=0.0, unit="cents")
    assert base != RegisterSpec(440.0, minimum=0.0, unit="Hz", accepts_signal=True)


def test_a_spec_cannot_be_edited_after_declaration():
    spec = RegisterSpec(1.0)
    with pytest.raises(Exception):
        spec.default = 2.0  # type: ignore[misc]


# -- scope and writes --------------------------------------------------


def test_scope_has_exactly_the_two_places_a_register_can_live():
    assert set(RegisterScope) == {RegisterScope.STAGE, RegisterScope.LANE}


def test_a_write_is_an_ordered_record():
    write = RegisterWrite(4800, "a stage", (("gate", True), ("frequency", 440.0)))
    assert write.timestamp == 4800
    assert write.values[0][0] == "gate"
    assert write.connection is None


def test_a_write_is_frozen_and_hashable():
    write = RegisterWrite(0, "a stage", (("gate", True),))
    assert hash(write) == hash(RegisterWrite(0, "a stage", (("gate", True),)))
    with pytest.raises(Exception):
        write.timestamp = 1  # type: ignore[misc]
