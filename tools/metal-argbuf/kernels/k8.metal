#include <metal_stdlib>
using namespace metal;
struct ABS { texture2d<float> t [[id(0)]]; sampler s [[id(1)]]; };
struct AB3 { texture3d<float> v [[id(0)]]; texturecube<float> c [[id(1)]]; texture2d<float, access::write> w [[id(2)]]; };
struct ABA { texture2d<float> t [[id(0)]]; };
struct ABB { texture2d<float> u [[id(0)]]; device float4 *p [[id(1)]]; };
kernel void s1(constant ABS &ab [[buffer(0)]], device float4 *out [[buffer(1)]], uint gid [[thread_position_in_grid]])
{ out[gid] = ab.t.sample(ab.s, float2(0.5, 0.5), level(0)); }
kernel void s2(constant ABA &ab [[buffer(0)]], sampler s [[sampler(0)]], device float4 *out [[buffer(1)]], uint gid [[thread_position_in_grid]])
{ out[gid] = ab.t.sample(s, float2(0.5, 0.5), level(0)); }
kernel void s3(constant AB3 &ab [[buffer(0)]], sampler s [[sampler(0)]], device float4 *out [[buffer(1)]], uint gid [[thread_position_in_grid]])
{ out[gid] = ab.v.sample(s, float3(0.5), level(0)) + ab.c.sample(s, float3(1, 0, 0), level(0)); ab.w.write(float4(1), uint2(gid, 0)); }
kernel void s4(constant ABA &a [[buffer(0)]], constant ABB &b [[buffer(2)]], device float4 *out [[buffer(1)]], uint gid [[thread_position_in_grid]])
{ out[gid] = a.t.read(uint2(0)) + b.u.read(uint2(0)) + b.p[0]; }
