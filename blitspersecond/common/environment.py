"""Strict boolean environment flags.

Shared because two packages parse `BPS_*` flags -- `system.config`
(`BPS_FULLSCREEN`) and `display.internal.pacing` (`BPS_FRAMEPACE_*`) -- and
they must agree on the spellings. They used to be separate copies with
subtly different signatures, which is a drift waiting to happen: a flag
accepting "yes" in one subsystem and rejecting it in another.
"""

import os


def environment_flag(name: str, default: bool = False) -> bool:
    """Read `name` as a boolean, or raise if it is not one.

    Strict on purpose: a flag silently defaulting on a typo is a harder bug
    to notice than one that refuses to start. An unset or empty variable is
    the default rather than an error -- `os.environ.get` and "exported but
    empty" are both ordinary ways to say nothing.
    """
    raw = os.environ.get(name)
    if raw is None or not raw:
        return default
    if raw.casefold() in {"1", "true", "yes", "on"}:
        return True
    if raw.casefold() in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean flag")
