#version 330
    in vec4 vertex_colors;
    in vec3 texture_coords;
    // Which palette of the bank to read. Flat because it is a row NUMBER --
    // there is nothing between row 2 and row 3 to interpolate towards -- and
    // flat is what keeps that true when it becomes per-quad data.
    flat in float vertex_palette_row;
    out vec4 final_colors;

    uniform sampler2D sprite_texture;   // unit 0: the indexed pixels
    uniform sampler2D palette_texture;  // unit 1: palette RAM, a 256xN RGBA table

    void main()
    {
        float palette_index = texture(sprite_texture, texture_coords.xy).r; // get the palette index from the sprite texture
        // Round, not truncate: the normalised byte k/255.0 scaled back by 255
        // must land on k for every k regardless of float rounding. The row
        // arrives as a small whole float and is rounded for the same reason.
        vec4 colour = texelFetch(
            palette_texture,
            ivec2(int(palette_index * 255.0 + 0.5), int(vertex_palette_row + 0.5)),
            0
        );
        // Multiply by vertex colors (tinting/fade parity with direct.frag;
        // draws that don't tint feed 1,1,1,1 so this costs nothing).
        final_colors = colour * vertex_colors;
    }
