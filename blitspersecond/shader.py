import os
from pyglet.graphics.shader import Shader, ShaderProgram
from pyglet.gl import (
    GL_BLEND,
    GL_TRIANGLES,
    GL_TEXTURE0,
    glActiveTexture,
    glBindTexture,
    glEnable,
    glBlendFunc,
    glDisable,
    GL_SRC_ALPHA,
    GL_ONE_MINUS_SRC_ALPHA,
)
from pyglet.graphics import Group
import pyglet

VERTEX_SHADER = "vertex.glsl"
FRAGMENT_SHADER = "fragment.glsl"


#####################################################
# Define a custom `Group` to encapsulate OpenGL state
#####################################################
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


#####################################################
# ShaderManager Class using RenderGroup for rendering
#####################################################
class ShaderManager:
    def __init__(self, fragment_shader: str):
        print(fragment_shader)
        # Create the vertex and fragment shaders using the Pyglet shader module
        self._fragment_shader = Shader(self._load_shader(fragment_shader), "fragment")
        self._vertex_shader = Shader(self._load_shader(VERTEX_SHADER), "vertex")

        # Create the shader program
        # self.program = ShaderProgram(self._fragment_shader, self._vertex_shader)
        self.program = ShaderProgram(self._vertex_shader, self._fragment_shader)

    def set_uniform(self, name: str, value):
        # Set the uniform value to the shader program
        self.program[name] = value

    def _load_shader(self, filepath):
        # Load the shader from a file, relative to the current directory
        full_path = os.path.join(os.path.dirname(__file__), filepath)
        with open(full_path, "r") as file:
            return file.read()

    def use(self):
        # Use the shader program (bind it for rendering)
        self.program.use()

    def render(self, tex, scale):
        # Create a batch and render with the shader program
        batch = pyglet.graphics.Batch()

        # Create a render group with the shader program
        group = RenderGroup(texture=tex, program=self.program)
        group.set_state()

        # Generate vertex data and indices for rendering a quad
        indices = (0, 1, 2, 0, 2, 3)
        vertex_positions = self.create_quad(0, 0, tex.width * scale, tex.height * scale)

        # Create the vertex list for rendering
        vertex_list = self.program.vertex_list_indexed(
            4,
            GL_TRIANGLES,
            indices,
            batch,
            group,
            position=("f", vertex_positions),
            tex_coords=("f", tex.tex_coords),
        )

        batch.draw()

    def create_quad(self, x, y, width, height):
        """Utility function to create 2D quad vertex positions."""
        return [
            x,
            y,  # Bottom-left corner
            x + width,
            y,  # Bottom-right corner
            x + width,
            y + height,  # Top-right corner
            x,
            y + height,  # Top-left corner
        ]
