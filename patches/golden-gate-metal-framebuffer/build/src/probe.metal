#include <metal_stdlib>
using namespace metal;

vertex float4 fullscreen(uint v [[vertex_id]]) {
    constexpr float2 p[] = {float2(-1, -1), float2(3, -1), float2(-1, 3)};
    return float4(p[v % 3], 0, 1);
}

struct IdentityVertex {
    float4 position [[position]];
    uint base_vertex_value [[flat]];
    uint base_instance_value [[flat]];
};
vertex IdentityVertex fullscreen_identity(uint v [[vertex_id]],uint base [[base_vertex]],uint instance [[base_instance]]) {
    constexpr float2 p[] = {float2(-1,-1),float2(3,-1),float2(-1,3)};
    return {float4(p[v%3],0,1),base,instance};
}

// Inputs are dyadic so the float formats have exact, independently known values.
float4 seed(float4 p, uint sample) {
    return float4(.125, .25, .375, .5) + float((uint(p.x) + 3 * uint(p.y)) % 4) / 32
           + float(sample) / 32;
}
float4 step(float4 x, uint sample) { return x + .0625f + float(sample) / 64; }

#define ATTACHMENT(N) \
struct Output##N { float4 value [[color(N)]]; }; \
fragment Output##N initial##N(float4 p [[position]], uint s [[sample_id]]) { \
    return {seed(p, s)}; \
} \
fragment Output##N fetch##N(float4 x [[color(N)]], uint s [[sample_id]]) { \
    return {step(x, s)}; \
} \
fragment Output##N texture##N(float4 p [[position]], texture2d<float, access::read> t [[texture(0)]]) { \
    return {step(t.read(uint2(p.xy)), 0)}; \
} \
fragment Output##N multisample##N(float4 p [[position]], uint s [[sample_id]], \
    texture2d_ms<float, access::read> t [[texture(0)]]) { \
    return {step(t.read(uint2(p.xy), s), s)}; \
}
ATTACHMENT(0)
ATTACHMENT(1)
ATTACHMENT(2)

fragment float4 fetch_identity(IdentityVertex v [[stage_in]],float4 x [[color(0)]],
                               uint primitive [[primitive_id]],uint sample [[sample_id]]) {
    return step(x,sample)+float(v.base_vertex_value+v.base_instance_value+primitive)/1024;
}

#ifdef FB_FUNCTION_CONSTANTS
constant uint attachment_index [[function_constant(0)]];
constant bool fetch_enabled [[function_constant(1)]];
struct DynamicOutput { float4 value [[color(attachment_index)]]; };
fragment DynamicOutput fetch_dynamic(float4 x [[color(attachment_index), function_constant(fetch_enabled)]],
                                      uint s [[sample_id]]) {
    return {step(fetch_enabled ? x : float4(.125,.25,.375,.5),s)};
}
#endif

kernel void read_single(texture2d<float, access::read> t [[texture(0)]],
                        device float4 *out [[buffer(0)]], uint2 p [[thread_position_in_grid]]) {
    if (p.x < 16 && p.y < 16) out[p.y * 16 + p.x] = t.read(p);
}
kernel void read_samples(texture2d_ms<float, access::read> t [[texture(0)]],
                        device float4 *out [[buffer(0)]], uint2 p [[thread_position_in_grid]]) {
    if (p.x < 16 && p.y < 16)
        for (uint s = 0; s < t.get_num_samples(); ++s)
            out[(p.y * 16 + p.x) * t.get_num_samples() + s] = t.read(p, s);
}
