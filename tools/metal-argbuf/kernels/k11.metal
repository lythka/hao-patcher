#include <metal_stdlib>
using namespace metal;
struct AS { texture2d<float> t [[id(0)]]; sampler s [[id(1)]]; texture2d<half> u [[id(2)]]; sampler s2 [[id(3)]]; };
kernel void ks(constant AS &ab [[buffer(0)]], device float4 *out [[buffer(1)]], uint gid [[thread_position_in_grid]])
{ out[gid] = ab.t.sample(ab.s, float2(0.25 + 0.5 * (gid & 1), 0.5), level(0)) + float4(ab.u.sample(ab.s2, float2(0.5), level(0))); }
