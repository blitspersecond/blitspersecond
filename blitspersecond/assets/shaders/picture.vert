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
    // The palette register: which row of the table on unit 1 the indexed
    // fragment stage reads. A whole picture has one palette, so the console
    // feeds 0 -- the row exists here because `indexed.frag` is shared with
    // the pillars that DO carry a bank, and one fragment stage reading the
    // row from a varying is cheaper than two fragment stages.
    in float palette_row;

    out vec4 vertex_colors;
    out vec3 texture_coords;
    flat out float vertex_palette_row;

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
        vertex_palette_row = palette_row;
    }
