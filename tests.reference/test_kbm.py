"""The local keyboard and mouse compose into one routing identity."""

import blitspersecond.input as input_api
from blitspersecond.input import kbm, keyboard, mouse
from blitspersecond.input.internal import InputDevice
from blitspersecond.input.kbm import Keyboard, KeyboardMouse, Mouse


def test_public_kbm_is_the_permanent_composite() -> None:
    assert input_api.kbm is kbm
    assert isinstance(kbm, KeyboardMouse)
    assert isinstance(kbm, InputDevice)
    assert KeyboardMouse() is kbm


def test_kbm_preserves_the_existing_device_surfaces() -> None:
    assert kbm.keyboard is keyboard
    assert kbm.mouse is mouse
    assert isinstance(keyboard, Keyboard)
    assert isinstance(mouse, Mouse)


def test_facets_are_not_independently_routable_devices() -> None:
    assert not isinstance(keyboard, KeyboardMouse)
    assert not isinstance(mouse, KeyboardMouse)
