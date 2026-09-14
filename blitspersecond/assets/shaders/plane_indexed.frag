#version 330
// The indexed plane stage: divide by `q`, wrap, sample the region for an
// index, then fetch that index from the layer's palette row. A transparent
// entry is discarded, so an indexed plane layer composes with blending off.
in vec4 vertex_colors;
in vec3 texture_coords;
flat in vec4 vertex_texture_region;
flat in float vertex_palette_row;
out vec4 final_colors;

uniform sampler2D sprite_texture;
uniform sampler2D palette_texture;

void main()
{
    if (abs(texture_coords.z) < 0.000001) {
        discard;
    }
    vec2 plane = texture_coords.xy / texture_coords.z;
    vec2 atlas = vertex_texture_region.xy
        + fract(plane) * vertex_texture_region.zw;
    float palette_index = texture(sprite_texture, atlas).r;
    vec4 color = texelFetch(
        palette_texture,
        ivec2(int(palette_index * 255.0 + 0.5), int(vertex_palette_row + 0.5)),
        0
    );
    if (color.a < 0.5) {
        discard;
    }
    final_colors = color * vertex_colors;
}
