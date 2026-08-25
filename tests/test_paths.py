"""PACKAGE_ROOT: the one place `blitspersecond/`'s own location gets computed.

Engine assets (shaders, charsets, ...) ship inside the package and must be
found regardless of the process's working directory or how the module that
wants one is nested. The old pattern -- each consuming module writing its
own `Path(__file__).resolve().parents[N]` -- makes N a silent liability: it
encodes that module's OWN depth in the package tree and goes stale the
moment the module moves (gl/shader.py already had to be corrected once, when
Shader moved out of graphics.internal). Anchoring everything through one
constant means only this module's depth ever needs to be right.
"""

from pathlib import Path

import blitspersecond
from blitspersecond.common import PACKAGE_ROOT


def test_package_root_is_the_blitspersecond_package_directory():
    """Checked against Python's own module system -- where `blitspersecond`
    itself was actually imported from -- rather than re-deriving the same
    `parents[N]` guess PACKAGE_ROOT exists to replace."""
    assert PACKAGE_ROOT == Path(blitspersecond.__file__).resolve().parent


def test_package_root_is_absolute():
    """Must not depend on the process's working directory -- the whole
    reason this exists is to survive being imported from anywhere."""
    assert PACKAGE_ROOT.is_absolute()


def test_package_root_contains_the_common_package_itself():
    assert (PACKAGE_ROOT / "common" / "paths.py").is_file()
