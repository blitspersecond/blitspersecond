"""The quad pillar: ordered projective mappings with convex screen clips.

    from blitspersecond.graphics.quads import Quads

    quads = Quads(atlas)
    quads.add(
        texture="STARTAN3",
        source=(32, 0, 64, 128),
        corners=(p1, p2, p3, p4),
        clip=viewport,
    )

`Quads` is a layer. One owns one `Atlas`, fixed for its lifetime, and draws
every command in insertion order with one texture bind and one draw call. A
command may select the whole named texture or an integer pixel rectangle
inside it. Four corners define one repetition of an infinite projective
texture plane; a convex clip selects the part that reaches the screen. With no
clip, the four corners provide the coverage too. It is deliberately
presentation only: there is no camera, world geometry, animation or collision
surface behind it.
"""

from .layer import QuadCommands, Quads

__all__ = ["QuadCommands", "Quads"]
