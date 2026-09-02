#version 330
    in vec4 colors;
    in vec3 tex_coords;
    in vec3 position;
    in vec3 translate;
    in vec2 scale;
    in float rotation;
    // The palette register: which row of the bank on unit 1 the indexed
    // fragment stage reads. A generic attribute for now -- set per draw, no
    // buffer -- so the day it becomes per-quad stream data is a buffer
    // binding here and nothing at all in the shaders.
    in float palette_row;

    out vec4 vertex_colors;
    out vec3 texture_coords;
    flat out float vertex_palette_row;

    uniform WindowBlock
    {
        mat4 projection;
        mat4 view;
    } window;

    mat4 m_scale = mat4(1.0);
    mat4 m_rotation = mat4(1.0);
    mat4 m_translate = mat4(1.0);

    void main()
    {
        m_scale[0][0] = scale.x;
        m_scale[1][1] = scale.y;
        m_translate[3][0] = translate.x;
        m_translate[3][1] = translate.y;
        m_translate[3][2] = translate.z;
        m_rotation[0][0] =  cos(-radians(rotation));
        m_rotation[0][1] =  sin(-radians(rotation));
        m_rotation[1][0] = -sin(-radians(rotation));
        m_rotation[1][1] =  cos(-radians(rotation));

        gl_Position = window.projection * window.view * m_translate * m_rotation * m_scale * vec4(position, 1.0);

        vertex_colors = colors;
        texture_coords = tex_coords;
        vertex_palette_row = palette_row;
    }
