#version 330 core
in vec2 position;
in vec2 tex_coords;
out vec2 TexCoord;

uniform WindowBlock
{
    mat4 projection;
    mat4 view;
} window;

void main()
{
    gl_Position = window.projection * window.view * vec4(position, 0.0, 1.0);
    TexCoord = vec2(tex_coords.x, 1.0 - tex_coords.y); // Flip the y-coordinate here
}