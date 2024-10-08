from typing import Callable
import cProfile
import pstats
from .shader import Shader, RenderGroup

import pyglet
from pyglet.gl import (
    glBindTexture,
    glTexParameteri,
    GL_TEXTURE_2D,
    GL_TEXTURE_MIN_FILTER,
    GL_NEAREST,
    GL_TEXTURE_MAG_FILTER,
    GL_TRIANGLES,
)

HEIGHT = 360
WIDTH = 640
SCALE = 1


class Display(object):
    def __init__(self, eventloop: pyglet.app.EventLoop, callback: Callable) -> None:
        self._window = pyglet.window.Window(
            WIDTH * SCALE,
            HEIGHT * SCALE,
            vsync=False,
        )
        self._callback = callback
        self._eventloop = eventloop

        # Shader setup
        self.vertex_code = Shader(
            """
        #version 330 core
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
        """,
            "vertex",
        )
        self.fragment_code = Shader(
            """
        #version 330 core
        in vec3 texture_coords;
        out vec4 final_colors;

        uniform sampler2D our_texture;

        void main()
        {
            final_colors = texture(our_texture, texture_coords.xy);
            // final_colors = vec4(255.0, 0.0, 255.0, 255.0);
        }
        """,
            "fragment",
        )

        profiler = cProfile.Profile()
        profiler.enable()

        @self._window.event
        def on_close():
            profiler.disable()
            stats = pstats.Stats(profiler)
            stats.strip_dirs().sort_stats("cumtime").print_stats(25)
            pyglet.clock.unschedule(self._callback)
            self._eventloop.exit()
            self._window.close()

    def update(self, texture: pyglet.image.Texture) -> None:
        self._window.clear()
        batch = pyglet.graphics.Batch()
        _t = texture
        _t.blit(0, 0)
        program = pyglet.graphics.shader.ShaderProgram(
            self.vertex_code, self.fragment_code
        )

        glBindTexture(GL_TEXTURE_2D, _t.id)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST)

        _t.height = HEIGHT * SCALE
        _t.width = WIDTH * SCALE

        group = RenderGroup(_t, program)
        group.set_state()

        indices = (0, 1, 2, 0, 2, 3)
        vertices = (0, 0, 640, 0, 640, 360, 0, 360)

        program.vertex_list_indexed(
            4,
            GL_TRIANGLES,
            indices,
            batch,
            group,
            position=("f", vertices),
            tex_coords=("f", _t.tex_coords),
        )

        batch.draw()

        self._window.dispatch_events()
        self._window.dispatch_event("on_draw")
        try:
            # self._window.flip()
            pass
        except AttributeError:
            print("window has been closed")


# def on_draw():
#     batch = pyglet.graphics.Batch()
#     tex = fb.display
#     tex.blit(0, 0)
#     program = ShaderProgram(vert_shader, crt_shader)
#     # program.pw_scale = PW_SCALE
#     program["pw_scale"] = PW_SCALE
#     program["scanline_intensity"] = 0.8
#     # program["grid_intensity"] = 0.1
#     # program["grid_spacing"] = 8
#     group = RenderGroup(tex, program)
#     group.set_state()

#     indices = (0, 1, 2, 0, 2, 3)
#     vertex_positions = create_quad(0, 0, tex)
#     # # count, mode, indices, batch, group, *data
#     vertex_list = program.vertex_list_indexed(
#         4,
#         GL_TRIANGLES,
#         indices,
#         batch,
#         group,
#         position=("f", vertex_positions),
#         tex_coords=("f", tex.tex_coords),
#     )
#     # get a texture to blit the crt shader too
#     batch.draw()


from pyglet.graphics.shader import Shader, ShaderProgram

# vert_shader = Shader(_vertex_source, "vertex")
# frag_shader = Shader(_fragment_source, "fragment")
# crt_shader = Shader(_crt_source, "fragment")
# program = ShaderProgram(vert_shader, crt_shader)
# program["pixels_per_scanline"] = int(PW_SCALE)

# @window.event
# def on_draw():
#     batch = pyglet.graphics.Batch()
#     tex = fb.display
#     tex.blit(0, 0)
#     program = ShaderProgram(vert_shader, crt_shader)
#     # program.pw_scale = PW_SCALE
#     program["pw_scale"] = PW_SCALE
#     program["scanline_intensity"] = 0.8
#     # program["grid_intensity"] = 0.1
#     # program["grid_spacing"] = 8
#     group = RenderGroup(tex, program)
#     group.set_state()

#     indices = (0, 1, 2, 0, 2, 3)
#     vertex_positions = create_quad(0, 0, tex)
#     # # count, mode, indices, batch, group, *data
#     vertex_list = program.vertex_list_indexed(
#         4,
#         GL_TRIANGLES,
#         indices,
#         batch,
#         group,
#         position=("f", vertex_positions),
#         tex_coords=("f", tex.tex_coords),
#     )
#     # get a texture to blit the crt shader too
#     batch.draw()
