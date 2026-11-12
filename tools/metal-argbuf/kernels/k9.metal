#include <metal_stdlib>
using namespace metal;
struct ABA { texture2d<float> t [[id(0)]]; };
struct AB3 { texture3d<float> v [[id(0)]]; };
struct ABC { texturecube<float> c [[id(0)]]; };
struct ABW { texture2d<float, access::write> w [[id(0)]]; };
kernel void s5(constant ABA &ab [[buffer(0)]], device float4 *out [[buffer(1)]], uint gid [[thread_position_in_grid]])
{ constexpr sampler s(filter::linear); out[gid] = ab.t.sample(s, float2(0.5, 0.5), level(0)); }
kernel void s6(constant AB3 &ab [[buffer(0)]], device float4 *out [[buffer(1)]], uint gid [[thread_position_in_grid]])
{ constexpr sampler s(filter::linear); out[gid] = ab.v.sample(s, float3(0.5), level(0)); }
kernel void s7(constant ABC &ab [[buffer(0)]], device float4 *out [[buffer(1)]], uint gid [[thread_position_in_grid]])
{ constexpr sampler s(filter::linear); out[gid] = ab.c.sample(s, float3(1, 0, 0), level(0)); }
kernel void s8(constant ABW &ab [[buffer(0)]], uint gid [[thread_position_in_grid]])
{ ab.w.write(float4(1), uint2(gid, 0)); }
kernel void s9(constant ABA &ab [[buffer(0)]], device float4 *out [[buffer(1)]], uint gid [[thread_position_in_grid]])
{ constexpr sampler s(filter::linear); out[gid] = ab.t.sample(s, float2(0.5, 0.5)); }
