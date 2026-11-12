#include <metal_stdlib>
using namespace metal;
struct AB { device float4 *p [[id(0)]]; device float4 *q [[id(1)]]; };
kernel void t5(constant AB &ab [[buffer(0)]], device float4 *out [[buffer(1)]], uint gid [[thread_position_in_grid]])
{ out[gid] = ab.p[gid & 3] + ab.q[1]; }
