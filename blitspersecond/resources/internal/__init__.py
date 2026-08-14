"""Resources appendix: machinery users never name.

`Resource` is the cached-text-file type behind `ResourceManager.fetch()` --
its one consumer is the graphics Shader pulling .vert/.frag source. The specs
users DO meet (ImageSpec, SoundSpec, Palette) stay in the package proper.
"""

from .resource import Resource

__all__ = ["Resource"]
