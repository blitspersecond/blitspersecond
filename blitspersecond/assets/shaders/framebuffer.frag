#version 330 core
in vec2 TexCoord;
out vec4 FragColor;

uniform sampler2D texture1;
uniform float scaleFactor;
uniform float viewportOriginY;
uniform vec3 colorMultiplier;
uniform float brightness;
uniform float contrast;
uniform float gamma;

void main()
{
    // Sample the texture
    vec4 color = texture(texture1, TexCoord);

    // Apply scanline effect
    float y = gl_FragCoord.y - viewportOriginY;
    float scanlineSpacing = scaleFactor;
    float scanlineThickness = scanlineSpacing * 0.2; // 20% of the spacing

    float t = fract(y / scanlineSpacing);
    if (t < (scanlineThickness / scanlineSpacing))
    {
        color.rgb *= 0.8;
    }

    // Apply color adjustments
    color.rgb *= colorMultiplier;

    // Adjust brightness and contrast
    color.rgb *= brightness;
    color.rgb = ((color.rgb - 0.5) * contrast) + 0.5;

    // Apply gamma correction
    color.rgb = pow(color.rgb, vec3(gamma));

    // Add noise
    float noise = fract(sin(dot(gl_FragCoord.xy ,vec2(12.9898,78.233))) * 43758.5453);
    color.rgb += noise * 0.02;

    FragColor = color;
}
