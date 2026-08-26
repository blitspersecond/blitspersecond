"""`environment_flag`: strict boolean parsing for every BPS_* variable.

Tested directly rather than only through its callers, because a caller reads
its flag once at construction -- these edge cases (an absent var, an exported
but empty one, a rejected typo) would otherwise only be reachable by
arranging the environment before the object exists.

Shared by `system.config` (BPS_FULLSCREEN) and the frame-pacing capture
(BPS_FRAMEPACE_*), which is why it lives in `common` rather than in either.
"""

import pytest

from blitspersecond.common import environment_flag


def test_absent_variable_is_the_default(monkeypatch):
    monkeypatch.delenv("BPS_PROBE", raising=False)
    assert environment_flag("BPS_PROBE") is False
    assert environment_flag("BPS_PROBE", default=True) is True


@pytest.mark.parametrize("raw", ["1", "true", "True", "TRUE", "yes", "on"])
def test_truthy_spellings(monkeypatch, raw):
    monkeypatch.setenv("BPS_PROBE", raw)
    assert environment_flag("BPS_PROBE") is True


@pytest.mark.parametrize("raw", ["0", "false", "False", "FALSE", "no", "off"])
def test_falsy_spellings(monkeypatch, raw):
    monkeypatch.setenv("BPS_PROBE", raw)
    assert environment_flag("BPS_PROBE") is False


def test_empty_string_is_the_default_not_an_error(monkeypatch):
    """`os.environ.get` returning None and "exported but empty" are both
    ordinary ways to say nothing, so neither may raise."""
    monkeypatch.setenv("BPS_PROBE", "")
    assert environment_flag("BPS_PROBE") is False
    monkeypatch.setenv("BPS_PROBE", "")
    assert environment_flag("BPS_PROBE", default=True) is True


def test_an_unrecognised_spelling_is_rejected(monkeypatch):
    """Strict on purpose: a flag silently defaulting on a typo is a harder
    bug to notice than one that refuses to start. The message names the
    variable, since that is the thing the operator has to go and fix."""
    monkeypatch.setenv("BPS_PROBE", "sometimes")
    with pytest.raises(ValueError, match="BPS_PROBE"):
        environment_flag("BPS_PROBE")
