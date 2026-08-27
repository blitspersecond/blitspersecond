#version 330 core
in vec2 in_position;
in vec2 in_tex_coords;
out vec2 TexCoord;

void main()
{
    gl_Position = vec4(in_position, 0.0, 1.0);
    TexCoord = vec2(in_tex_coords.x, 1.0 - in_tex_coords.y);
}
