#version 330
    in vec4 vertex_colors;
    in vec3 texture_coords;
    out vec4 final_colors;

    uniform sampler2D sprite_texture;   // unit 0: the indexed pixels
    uniform sampler2D palette_texture;  // unit 1: palette RAM, one 256x1 RGBA row

    void main()
    {
        float palette_index = texture(sprite_texture, texture_coords.xy).r; // get the palette index from the sprite texture
        // Round, not truncate: the normalised byte k/255.0 scaled back by 255
        // must land on k for every k regardless of float rounding.
        vec4 colour = texelFetch(palette_texture, ivec2(int(palette_index * 255.0 + 0.5), 0), 0);
        // Multiply by vertex colors (tinting/fade parity with direct.frag;
        // draws that don't tint feed 1,1,1,1 so this costs nothing).
        final_colors = colour * vertex_colors;
    }
