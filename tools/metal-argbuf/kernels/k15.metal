#include <metal_stdlib>
using namespace metal;
struct VA { device const float4 *pos [[id(0)]]; constant float4 *scale [[id(1)]]; };
struct VA2 { constant float4 *pos [[id(0)]]; };
struct FA { texture2d<float> t [[id(0)]]; constant float4 *k [[id(1)]]; texture2d<float> u [[id(2)]]; };
struct FT { texture2d<float> t [[id(0)]]; };
struct FK { constant float4 *k [[id(0)]]; };
struct VO { float4 p [[position]]; };
vertex VO vs(uint vid [[vertex_id]], constant VA &va [[buffer(3)]]) { VO o; o.p = va.pos[vid] * va.scale[0]; return o; }
vertex VO vs2(uint vid [[vertex_id]], constant VA2 &va [[buffer(3)]]) { VO o; o.p = va.pos[vid]; return o; }
vertex VO vplain(uint vid [[vertex_id]], constant float4 *pos [[buffer(3)]]) { VO o; o.p = pos[vid]; return o; }
fragment float4 fs(VO in [[stage_in]], constant FA &fa [[buffer(5)]]) { return fa.t.read(uint2(0, 0)) + fa.k[1] + fa.u.read(uint2(0, 0)); }
fragment float4 ft(VO in [[stage_in]], constant FT &fa [[buffer(5)]]) { return fa.t.read(uint2(0, 0)); }
fragment float4 fk(VO in [[stage_in]], constant FK &fa [[buffer(5)]]) { return fa.k[1]; }
fragment float4 fplain(VO in [[stage_in]]) { return float4(1); }
