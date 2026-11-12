#include <metal_stdlib>
using namespace metal;
struct AB { texture2d<float> t [[id(0)]]; device float4 *b [[id(1)]]; };
kernel void t1(constant AB &ab [[buffer(0)]], device float4 *out [[buffer(1)]], uint gid [[thread_position_in_grid]])
{ out[gid] = ab.t.read(uint2(0, 0)) + ab.b[0]; }
kernel void t2(array<texture2d<float>, 8> texs [[texture(0)]], device const uint *idx [[buffer(0)]],
               device float4 *out [[buffer(1)]], uint gid [[thread_position_in_grid]])
{ out[gid] = texs[idx[gid]].read(uint2(0, 0)); }
