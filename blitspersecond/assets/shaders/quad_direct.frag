#version 330
in vec4 vertex_colors;
in vec3 texture_coords;
flat in vec4 vertex_texture_region;
out vec4 final_colors;

uniform sampler2D sprite_texture;

void main()
{
    if (abs(texture_coords.z) < 0.000001) {
        discard;
    }
    vec2 plane = texture_coords.xy / texture_coords.z;
    vec2 atlas = vertex_texture_region.xy
        + fract(plane) * vertex_texture_region.zw;
    final_colors = texture(sprite_texture, atlas) * vertex_colors;
}
