#version 330 core

in vec2 fragTexCoords;
out vec4 color;

uniform sampler2D tex;
uniform float pw_scale;
uniform float scanline_intensity;
uniform float luminance_boost;
uniform float glow_intensity;

void main()
{
    // Basic texture color
    vec4 texColor = texture(tex, fragTexCoords);

    // Simulate scanlines
    float scanline = mod(gl_FragCoord.y / pw_scale, 2.0) < 1.0 ? scanline_intensity : 1.0;
    
    // Boost luminance
    texColor.rgb *= luminance_boost;
    
    // Apply glow effect (example, simple blur)
    float glow = smoothstep(0.0, 1.0, texColor.r) * glow_intensity;

    // Combine everything
    color = vec4(texColor.rgb * scanline + glow, texColor.a);
}