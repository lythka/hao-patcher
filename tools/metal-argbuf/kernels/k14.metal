#include <metal_stdlib>
using namespace metal;
struct VA { device const float4 *pos [[id(0)]]; constant float4 *scale [[id(1)]]; };
struct FA { texture2d<float> t [[id(0)]]; constant float4 *k [[id(1)]]; texture2d<float> u [[id(2)]]; };
struct VO { float4 p [[position]]; };
vertex VO vs(uint vid [[vertex_id]], constant VA &va [[buffer(3)]]) { VO o; o.p = va.pos[vid] * va.scale[0]; return o; }
fragment float4 fs(VO in [[stage_in]], constant FA &fa [[buffer(5)]]) {
  return fa.t.read(uint2(0, 0)) + fa.k[1] + fa.u.read(uint2(0, 0)); }
