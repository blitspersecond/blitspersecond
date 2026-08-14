#version 330
in vec4 vertex_colors;
in vec3 texture_coords;
out vec4 final_colors;

uniform sampler2D sprite_texture;
// uniform vec4[256] palette;

void main()
{
    vec4 texColor = texture(sprite_texture, texture_coords.xy);

    //texColor.rgb = (1.0 - texColor.rgb);
    // Multiply by vertex colors (allows for tinting and alpha)
    final_colors = texColor * vertex_colors;
}


