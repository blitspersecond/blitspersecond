from pyglet.gl import *
import ctypes

QUAD = (0, 0, 640, 0, 640, 360, 0, 360)


class Shader:
    def __init__(self, vertex_code: str, fragment_code: str):
        # Create vertex shader
        vertex_shader = glCreateShader(GL_VERTEX_SHADER)
        source = ctypes.create_string_buffer(vertex_code.encode("utf-8"))
        length = ctypes.c_int(len(vertex_code))
        glShaderSource(
            vertex_shader,
            1,
            ctypes.cast(
                ctypes.pointer(ctypes.pointer(source)),
                ctypes.POINTER(ctypes.POINTER(GLchar)),
            ),
            ctypes.byref(length),
        )
        glCompileShader(vertex_shader)

        # Check for compile errors
        if not self._check_compile_errors(vertex_shader, "VERTEX"):
            print("Vertex shader compilation failed.")

        # Create fragment shader
        fragment_shader = glCreateShader(GL_FRAGMENT_SHADER)
        source = ctypes.create_string_buffer(fragment_code.encode("utf-8"))
        length = ctypes.c_int(len(fragment_code))
        glShaderSource(
            fragment_shader,
            1,
            ctypes.cast(
                ctypes.pointer(ctypes.pointer(source)),
                ctypes.POINTER(ctypes.POINTER(GLchar)),
            ),
            ctypes.byref(length),
        )
        glCompileShader(fragment_shader)

        # Check for compile errors
        if not self._check_compile_errors(fragment_shader, "FRAGMENT"):
            print("Fragment shader compilation failed.")

        # Link shaders to create the program
        self.program = glCreateProgram()
        glAttachShader(self.program, vertex_shader)
        glAttachShader(self.program, fragment_shader)
        glLinkProgram(self.program)

        # Check for linking errors
        if not self._check_compile_errors(self.program, "PROGRAM", is_program=True):
            print("Shader program linking failed.")

        # Cleanup: detach and delete shaders as they are no longer needed
        glDeleteShader(vertex_shader)
        glDeleteShader(fragment_shader)

    def use(self):
        glUseProgram(self.program)

    def _check_compile_errors(self, shader, shader_type, is_program=False):
        if is_program:
            success = GLint()
            glGetProgramiv(shader, GL_LINK_STATUS, ctypes.byref(success))
            if not success:
                info_log = ctypes.create_string_buffer(1024)
                glGetProgramInfoLog(shader, 1024, None, info_log)
                print(
                    f"{shader_type} PROGRAM LINKING ERROR:\n{info_log.value.decode()}"
                )
                return False
        else:
            success = GLint()
            glGetShaderiv(shader, GL_COMPILE_STATUS, ctypes.byref(success))
            if not success:
                info_log = ctypes.create_string_buffer(1024)
                glGetShaderInfoLog(shader, 1024, None, info_log)
                print(
                    f"{shader_type} SHADER COMPILATION ERROR:\n{info_log.value.decode()}"
                )
                return False
        return True


_vertex_source = """#version 330 core
    in vec2 position;
    in vec3 tex_coords;
    out vec3 texture_coords;

    uniform WindowBlock
    {                       // This UBO is defined on Window creation, and available
        mat4 projection;    // in all Shaders. You can modify these matrixes with the
        mat4 view;          // Window.view and Window.projection properties.
    } window;

    void main()
    {
        gl_Position = window.projection * window.view * vec4(position, 1, 1);
        texture_coords = tex_coords;
    }
"""

_fragment_source = """#version 330 core
    in vec3 texture_coords;
    out vec4 final_colors;

    uniform sampler2D our_texture;

    void main()
    {
        final_colors = texture(our_texture, texture_coords.xy);
        // final_colors = vec4(255.0, 0.0, 255.0, 255.0);
    }
"""

_crt_source = """#version 330 core

in vec3 texture_coords;
out vec4 final_colors;

uniform int pw_scale;
uniform sampler2D our_texture;
uniform float scanline_intensity;
uniform float glow_intensity = 0.2;
uniform float glow_threshold = 0.6;

void main()
{
    // Calculate the scanline number for the current fragment
    int scanline_number = int(floor(gl_FragCoord.y / float(pw_scale) * 4.0));

    // Adjust scanline thickness and intensity
    float line_intensity = 1.0 - scanline_intensity; // Adjust the intensity of the scanlines

    // Darken alternating scanlines
    vec4 scanline_color;
    if (mod(scanline_number, 2) == 1) {
        // Apply darker color or reduce brightness with adjusted intensity
        scanline_color = vec4(line_intensity) * texture(our_texture, texture_coords.xy);
    } else {
        scanline_color = texture(our_texture, texture_coords.xy);
    }

    // Compute luminance and glow
    float luminance = dot(scanline_color.rgb, vec3(0.299, 0.587, 0.114));
    vec4 glow_color = vec4(scanline_color.rgb, 1.0) * (luminance > glow_threshold ? glow_intensity : 0.0);

    final_colors = scanline_color + glow_color;
}
"""

_crt_source2 = """#version 330 core
    in vec3 texture_coords;
    out vec4 final_colors;

    void main()
    {
        // Calculate pixel grid coordinates
        vec2 grid_coords = floor(texture_coords.xy * vec2(pw_scale)) / vec2(pw_scale);

        // Apply pixel grid overlay
        vec4 grid_color = vec4(0.0);
        if (mod(grid_coords.x * grid_spacing, 1.0) < grid_intensity || mod(grid_coords.y * grid_spacing, 1.0) < grid_intensity) {
            grid_color = vec4(1.0); // Set grid pixels to white (or adjust grid color)
        }

        // Combine scanline and grid colors
        final_colors = scanline_color + grid_color;
    }
"""

from pyglet.gl import *
from pyglet.graphics import Group


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
