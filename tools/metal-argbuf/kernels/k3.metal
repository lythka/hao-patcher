#include <metal_stdlib>
using namespace metal;
struct AB { texture2d<float> a [[id(0)]]; texture2d<float> b [[id(1)]]; };
kernel void t3(constant AB &ab [[buffer(0)]], device float4 *out [[buffer(1)]], uint gid [[thread_position_in_grid]])
{ out[gid] = (gid & 1) ? ab.b.read(uint2(0, 0)) : ab.a.read(uint2(0, 0)); }
kernel void t4(texture2d<float> a [[texture(16)]], texture2d<float> b [[texture(17)]], device float4 *out [[buffer(1)]], uint gid [[thread_position_in_grid]])
{ out[gid] = (gid & 1) ? b.read(uint2(0, 0)) : a.read(uint2(0, 0)); }
