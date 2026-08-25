#version 330
    // The static engine's vertex stage: one whole-buffer quad. No
    // scale/rotation sprite gymnastics -- the engine draws a full untouched
    // file wherever it sits: `translate` is the image's (x, y), a generic
    // attribute so moving it writes no buffers. `colors` carries the
    // per-instance red/green/blue/alpha multipliers (tint/fade).
    in vec4 colors;
    in vec3 tex_coords;
    in vec3 position;
    in vec3 translate;

    out vec4 vertex_colors;
    out vec3 texture_coords;

    uniform WindowBlock
    {
        mat4 projection;
        mat4 view;
    } window;

    void main()
    {
        gl_Position = window.projection * window.view * vec4(position + translate, 1.0);
        vertex_colors = colors;
        texture_coords = tex_coords;
    }
