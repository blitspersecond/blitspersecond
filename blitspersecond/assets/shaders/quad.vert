#version 330
in vec3 tex_coords;
in vec3 position;
in vec4 texture_region;
in float palette_row;

out vec4 vertex_colors;
out vec3 texture_coords;
flat out vec4 vertex_texture_region;
flat out float vertex_palette_row;

uniform WindowBlock
{
    mat4 projection;
    mat4 view;
} window;

void main()
{
    // Positions are already final screen-space corners. There is no local
    // translate, scale or rotation stage in the Quad pillar.
    gl_Position = window.projection * window.view * vec4(position, 1.0);
    vertex_colors = vec4(1.0);
    texture_coords = tex_coords;
    vertex_texture_region = texture_region;
    vertex_palette_row = palette_row;
}
