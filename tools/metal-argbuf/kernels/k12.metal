#include <metal_stdlib>
using namespace metal;
struct AS { texture2d<float> t [[id(0)]]; sampler s [[id(1)]]; };
kernel void kp(texture2d<float> tx [[texture(2)]], sampler sm [[sampler(3)]], sampler sm2 [[sampler(5)]], constant AS &ab [[buffer(0)]],
               device float4 *out [[buffer(1)]], uint gid [[thread_position_in_grid]])
{ out[gid] = tx.sample(sm, float2(0.5), level(0)) + tx.sample(sm2, float2(0.25), level(0)) + ab.t.sample(ab.s, float2(0.5), level(0)); }
