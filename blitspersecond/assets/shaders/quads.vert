#version 330
    // The sprite pillar's vertex stage: NO vertex attributes at all.
    //
    // Every quad is nine floats in a buffer texture the vertex stage reads,
    // and each of the four vertices works out which quad it belongs to and
    // which corner it is from `gl_VertexID` alone. The index buffer is still `[0,1,2,0,2,3] + 4i`,
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
    // ## Where the records live
    //
    // A BUFFER TEXTURE, which is why this is `#version 330` like every other
    // shader here. A shader storage buffer would do the same job and needs
    // GL 4.3 -- above the rest of the engine, and above macOS at 4.1. Both
    // were built and measured, four samples each in one session: 15.16ms
    // against 15.32ms at 250,000 presentations, about 1%, with the ranges
    // overlapping. Kris: *"I don't think the complexity of maintaining two
    // paths warrants the marginal performance gain"*. So the SSBO went and
    // this stayed, and the geometry below is written once because there is
    // now only one place it could be written.
    //
    // R32F, so one fetch is one float and the record needs no padding.
    // RGBA32F was tried too -- three fetches a quad instead of nine, at the
    // price of rounding nine floats up to twelve -- and it is 3.6% SLOWER:
    // the extra third of writing costs more than the better fetch saves,
    // which is the answer this pillar gives every time. The CPU side is the
    // one that costs.
    uniform samplerBuffer quad_data;

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

        vec2 origin = vec2(
            texelFetch(quad_data, base).r, texelFetch(quad_data, base + 1).r
        );
        vec2 size = vec2(
            texelFetch(quad_data, base + 2).r, texelFetch(quad_data, base + 3).r
        );
        vec2 uv0 = vec2(
            texelFetch(quad_data, base + 4).r, texelFetch(quad_data, base + 5).r
        );
        vec2 duv = vec2(
            texelFetch(quad_data, base + 6).r, texelFetch(quad_data, base + 7).r
        );
        int turn = int(texelFetch(quad_data, base + 8).r + 0.5);

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
