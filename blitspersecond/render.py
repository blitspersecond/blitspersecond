from pyglet.gl import (
    glActiveTexture,
    glBindTexture,
    glBlendFunc,
    glDisable,
    glEnable,
    glTexParameteri,
    GL_BLEND,
    GL_SRC_ALPHA,
    GL_ONE_MINUS_SRC_ALPHA,
    GL_TEXTURE_2D,
    GL_TEXTURE_MIN_FILTER,
    GL_NEAREST,
    GL_TEXTURE_MAG_FILTER,
    GL_TEXTURE0,
    GL_TRIANGLES,
)

from .config import Config
from pyglet.graphics import Batch, Group
from pyglet.graphics.shader import Shader, ShaderProgram
from pyglet.image import Texture


class RenderGroup(Group):
    """A Group that enables and binds a Texture and ShaderProgram.

    RenderGroups are equal if their Texture and ShaderProgram
    are equal.
    """

    def __init__(self, texture, program, order=0, parent=None):
        """Create a RenderGroup.

        :Parameters:
            `texture` : `~pyglet.image.Texture`
                Texture to bind.
            `program` : `~pyglet.graphics.shader.ShaderProgram`
                ShaderProgram to use.
            `order` : int
                Change the order to render above or below other Groups.
            `parent` : `~pyglet.graphics.Group`
                Parent group.
        """
        super().__init__(order, parent)
        self.texture = texture
        self.program = program

    def set_state(self):
        glActiveTexture(GL_TEXTURE0)
        glBindTexture(self.texture.target, self.texture.id)
        glEnable(GL_BLEND)
        glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
        self.program.use()

    def unset_state(self):
        glDisable(GL_BLEND)

    def __hash__(self):
        return hash(
            (
                self.texture.target,
                self.texture.id,
                self.order,
                self.parent,
                self.program,
            )
        )

    def __eq__(self, other):
        return (
            self.__class__ is other.__class__
            and self.texture.target == other.texture.target
            and self.texture.id == other.texture.id
            and self.order == other.order
            and self.program == other.program
            and self.parent == other.parent
        )


vertex_code = """
#version 330 core
in vec2 position;
in vec2 tex_coords;
out vec2 TexCoord;

uniform WindowBlock
{
    mat4 projection;
    mat4 view;
} window;

void main()
{
    gl_Position = window.projection * window.view * vec4(position, 0.0, 1.0);
    TexCoord = vec2(tex_coords.x, 1.0 - tex_coords.y); // Flip the y-coordinate here
}
"""

fragment_code = """
#version 330 core
in vec2 TexCoord;
out vec4 FragColor;

uniform sampler2D texture1;

void main()
{
    FragColor = texture(texture1, TexCoord); // Use TexCoord directly, no flip here
}
"""


class Renderer:
    def __init__(self):
        self._c = Config()
        self._width = self._c.window.width
        self._height = self._c.window.height
        self._scale = self._c.window.scale
        self._indices = (0, 1, 2, 0, 2, 3)
        self._vertices = (
            0,
            0,
            1 * self._width * self._scale,
            0,
            1 * self._width * self._scale,
            1 * self._height * self._scale,
            0,
            self._height * self._scale,
        )
        self._coords = (0, 0, 1, 0, 1, 1, 0, 1)

        vertex_shader = Shader(vertex_code, "vertex")
        fragment_shader = Shader(fragment_code, "fragment")
        self._program = ShaderProgram(vertex_shader, fragment_shader)

    def render(self, texture: Texture):
        batch = Batch()

        texture.height = self._height * self._scale
        texture.width = self._width * self._scale

        glBindTexture(GL_TEXTURE_2D, texture.id)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST)

        group = RenderGroup(texture, self._program)
        group.set_state()

        self._program.vertex_list_indexed(
            4,
            GL_TRIANGLES,
            self._indices,
            batch,
            group,
            position=("f", self._vertices),
            tex_coords=("f", self._coords),
        )

        batch.draw()
        group.unset_state()
