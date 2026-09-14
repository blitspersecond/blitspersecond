#version 330
// The quad pillar's vertex stage: a clipped plane rather than a quad, which
// is what the file is named for. The sprite pillar's `quads.vert` is a
// different stage entirely and shares nothing with this one.
//
// A vertex here is a corner of the COVERAGE polygon -- the command's clip, or
// its four mapping corners when it has no clip -- and carries the homogeneous
// plane coordinate `(u*q, v*q, q)` that its screen position solved to. Those
// three interpolate linearly across any triangulation of that polygon, so the
// fan may have any number of points and still sample one continuous
// projective plane. The divide by `q` happens per fragment, not here.
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
