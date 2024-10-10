#version 330 core
in vec2 TexCoord;
out vec4 FragColor;

uniform sampler2D texture1;
uniform float scaleFactor;      // The scaling factor applied to the texture
uniform vec2 resolution;        // The resolution of the screen or window

void main()
{
    // Fetch the original texture color
    vec4 color = texture(texture1, TexCoord);

    // Calculate the pixel position in screen space
    float y = TexCoord.y * (360 * 4); // 360 is the internal resolution 4 is scaleFactor

    // Apply scanline effect by darkening every other line
    if (mod(floor(y), 4.0) == 0.0) // 4 is the scale factor
    {
        color.rgb *= 0.2; // Adjust this value to control the darkness of the scanline
    }

    // Vignette effect
    // float dist = distance(TexCoord, vec2(0.5, 0.5));
    // float vignette = smoothstep(0.3, 0.8, pow(dist, 1.2));
    // color.rgb *= (1.0 - vignette * 0.8);

    float dist = distance(TexCoord, vec2(0.5, 0.5));
    float vignette = smoothstep(0.5, 0.9, pow(dist, 1.1));
    color.rgb *= (1.0 - vignette * 0.8);


    // Color tinting
    color.rgb *= vec3(1.2, 1.0, 0.8); // Increase red, normal green, decrease blue

    // Output the final color
    FragColor = color;
}