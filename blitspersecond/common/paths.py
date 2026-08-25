from pathlib import Path

# `blitspersecond/` itself -- the directory ENGINE assets (shaders, charsets,
# ...) hang off. Resolved ONCE, here, from THIS file's own known depth
# (common/paths.py -> blitspersecond/) -- so only this line needs to track
# a depth in the package tree, not every module that reaches for an asset.
# The alternative, `Path(__file__).resolve().parents[N]` written out again
# at each call site, silently goes stale the moment that module moves; it
# already had to be corrected once, in gl/shader.py, when Shader moved out
# of graphics.internal.
PACKAGE_ROOT: Path = Path(__file__).resolve().parents[1]
