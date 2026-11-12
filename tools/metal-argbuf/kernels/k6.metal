#include <metal_stdlib>
using namespace metal;
kernel void t6(device float4 *a [[buffer(23)]], device float4 *b [[buffer(24)]], device float4 *out [[buffer(1)]], uint gid [[thread_position_in_grid]])
{ out[gid] = a[gid & 3] + b[2]; }
