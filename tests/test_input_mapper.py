"""Pad families: names and labels, and the selector that never rejects.

See `docs/bps/PADS_PORT.md`, *Retained rulings*.
"""

import re

import pytest

from blitspersecond.input.pads import Button, PadMapper, mapper_for, model_for
from blitspersecond.input.pads.mapper import (
    _MODEL_MAPPERS,
    _MODELS,
    _VENDORS,
    DualSenseMapper,
    NintendoMapper,
    SonyMapper,
    XboxMapper,
)

FAMILIES = (XboxMapper, SonyMapper, DualSenseMapper, NintendoMapper)


@pytest.mark.parametrize("family", FAMILIES, ids=lambda f: f.__name__)
def test_every_family_labels_every_button(family):
    # A family that forgets one silently falls back to the position name, and
    # a HUD then prompts "press FACE_NORTH" at somebody.
    missing = [button for button in Button if button not in family.LABELS]
    assert not missing, f"{family.__name__} has no label for {missing}"


def test_nintendo_prints_the_same_four_letters_rotated():
    # The clearest case for why Button names positions. A vocabulary built on
    # letters would have Nintendo's A and Microsoft's A mean different thumbs.
    assert XboxMapper.label(Button.FACE_SOUTH) == "A"
    assert NintendoMapper.label(Button.FACE_SOUTH) == "B"
    assert NintendoMapper.label(Button.FACE_EAST) == "A"
    assert {NintendoMapper.label(b) for b in Button if b.name.startswith("FACE")} == {
        XboxMapper.label(b) for b in Button if b.name.startswith("FACE")
    }


def test_accept_is_a_family_convention_not_a_constant():
    assert XboxMapper.ACCEPT is Button.FACE_SOUTH
    assert SonyMapper.ACCEPT is Button.FACE_SOUTH
    assert NintendoMapper.ACCEPT is Button.FACE_EAST
    assert all(family.ACCEPT in Button for family in FAMILIES)


def test_the_dualsense_differs_from_its_parent_by_one_label():
    differences = {
        button
        for button in Button
        if DualSenseMapper.label(button) != SonyMapper.label(button)
    }
    assert differences == {Button.SELECT}
    assert DualSenseMapper.label(Button.SELECT) == "Create"


# -- the selector ---------------------------------------------------------


def test_an_unknown_pad_is_an_xbox_pad_rather_than_an_error():
    # The duck test. Not a fallback that mostly works: the modal answer.
    assert mapper_for("1234", "5678") is XboxMapper
    assert model_for("1234", "5678") is None


def test_a_known_vendor_answers_for_a_model_it_has_never_seen():
    # An unreleased Sony pad still says "cross", because that is a fact about
    # Sony rather than about the model.
    assert mapper_for("054c", "ffff") is SonyMapper
    assert mapper_for("054c", "ffff").label(Button.FACE_SOUTH) == "cross"
    assert model_for("054c", "ffff") is None


def test_a_model_can_refine_its_vendors_family():
    assert mapper_for("054c", "05c4") is SonyMapper  # DualShock 4
    assert mapper_for("054c", "0ce6") is DualSenseMapper  # DualSense


def test_the_name_table_decides_nothing():
    # Hori is in _MODELS and in no mapper table, so it has an editorial name
    # and deliberately takes the ordinary Xbox fallback. This is the proof
    # that model_for and mapper_for are independent.
    assert model_for("0f0d", "0150") == "Hori Fighting Commander (Xbox)"
    assert mapper_for("0f0d", "0150") is XboxMapper
    assert "0f0d" not in _VENDORS


def test_selection_is_case_insensitive_because_proc_and_win32_disagree():
    assert mapper_for("054C", "0CE6") is DualSenseMapper
    assert model_for("045E", "0B13") == "Xbox Series Controller"


# -- the tables themselves ------------------------------------------------


def test_steam_is_absent_and_must_stay_absent():
    # Steam hides identity: a virtual pad is a proxy standing in front of a
    # real device and concealing which one, so there is nothing to report
    # rather than something reported badly. Refusing the vendor is how a
    # physical pad is seen once, as itself, instead of once directly and again
    # through a proxy that answers to input and looks like it works.
    assert "28de" not in _VENDORS
    assert not any(key.startswith("28de") for key in _MODELS)
    assert not any(key.startswith("28de") for key in _MODEL_MAPPERS)


@pytest.mark.parametrize("table", [_MODELS, _MODEL_MAPPERS], ids=["models", "mappers"])
def test_keys_are_lowercase_vendor_colon_product(table):
    # /proc prints them lowercase; a stray capital silently never matches.
    for key in table:
        assert re.fullmatch(r"[0-9a-f]{4}:[0-9a-f]{4}", key), key


def test_the_behavioural_tables_stay_small():
    # Six entries decide anything; the 29 are editorial. If this grows, the
    # question is whether the pad was in your hands -- see PADS_PORT.md,
    # "the admission rule is physical possession".
    assert len(_VENDORS) + len(_MODEL_MAPPERS) == 6
    assert all(issubclass(f, PadMapper) for f in _VENDORS.values())
    assert all(issubclass(f, PadMapper) for f in _MODEL_MAPPERS.values())


def test_every_model_mapper_belongs_to_the_vendor_it_is_keyed_under():
    for key, family in _MODEL_MAPPERS.items():
        vendor = key.split(":")[0]
        assert issubclass(family, _VENDORS[vendor]), key
