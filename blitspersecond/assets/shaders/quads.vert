#version 430
    // The sprite pillar's vertex stage: NO vertex attributes at all.
    //
    // Every quad is nine floats in a shader storage buffer, and each of the
    // four vertices works out which quad it belongs to and which corner it is
    // from `gl_VertexID` alone. The index buffer is still `[0,1,2,0,2,3] + 4i`,
    // so `gl_VertexID` takes values 0..4N-1 -- four invocations per quad, not
    // six, which is the whole reason to keep drawing elements rather than
    // arrays.
    //
    // ## Why this exists
    //
    // The frame plan used to expand these nine floats into four corners of
    // three floats, twice -- 96 bytes of vertex stream manufactured per quad
    // out of the 13 bytes of state that determine it. At 250,000
    // presentations that was 24 MB built and shipped every frame, and the
    // building was 96% of a sprite frame. It is 36 bytes now and the
    // expansion happens here, where a vertex stage is running anyway.
    //
    // ## The layout
    //
    //   0..3   x, y, w, h     where the quad lands, already turned
    //   4..7   u0, v0, du, dv the source box, NOT turned
    //   8      turn           which of the eight orientations
    //
    // The rect arrives turned because the CPU has to know the turned width
    // and height anyway -- `local_rect` is shared with collision stamping and
    // must stay one function. The UV box arrives UNTURNED because turning a
    // quad permutes which corner reads which (u, v) without moving the box,
    // so the eight-fold table the CPU used to gather twelve floats from
    // collapses to four floats per assembly.
    layout(std430, binding = 0) readonly buffer QuadData { float quads[]; };

    uniform WindowBlock
    {
        mat4 projection;
        mat4 view;
    } window;

    // Per RUN, not per vertex. Colour belongs to the sprite and the row to a
    // stretch of presentations, so neither is worth a stream -- see
    // `Presentation.red` and `frame.py`.
    uniform vec4 run_colors;
    uniform float run_palette_row;

    out vec4 vertex_colors;
    out vec3 texture_coords;
    flat out float vertex_palette_row;

    void main()
    {
        int quad = gl_VertexID / 4;
        int corner = gl_VertexID % 4;
        int base = quad * 9;

        vec2 origin = vec2(quads[base], quads[base + 1]);
        vec2 size = vec2(quads[base + 2], quads[base + 3]);
        vec2 uv0 = vec2(quads[base + 4], quads[base + 5]);
        vec2 duv = vec2(quads[base + 6], quads[base + 7]);
        int turn = int(quads[base + 8] + 0.5);

        // Corners wind top-left, top-right, bottom-right, bottom-left -- the
        // same winding the index pattern and the old staging both assumed.
        vec2 xy = origin + size * vec2(
            (corner == 1 || corner == 2) ? 1.0 : 0.0,
            (corner >= 2) ? 1.0 : 0.0
        );

        // The turn, applied by choosing which corner reads which (u, v).
        // `_corners` in frame.py composes transpose, then FLIP_H, then
        // FLIP_V; each maps an output corner to an input one, so undoing them
        // to find the source runs in the opposite order -- V, then H, then
        // transpose. Getting that backwards is invisible on a square,
        // symmetric source and wrong on everything else.
        int source = corner;
        if ((turn & 4) != 0) { source = 3 - source; }          // FLIP_V
        if ((turn & 2) != 0) { source = source ^ 1; }          // FLIP_H
        if ((turn & 1) != 0) { source = (4 - source) % 4; }    // TRANSPOSE

        vec2 uv = uv0 + duv * vec2(
            (source == 1 || source == 2) ? 1.0 : 0.0,
            (source >= 2) ? 1.0 : 0.0
        );

        gl_Position = window.projection * window.view * vec4(xy, 0.0, 1.0);
        texture_coords = vec3(uv, 0.0);
        vertex_colors = run_colors;
        vertex_palette_row = run_palette_row;
    }
