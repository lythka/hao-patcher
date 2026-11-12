#include <metal_stdlib>
using namespace metal;
struct Inner { texture2d<float> a [[id(0)]]; constant float4 *b [[id(1)]]; };
struct AB { texture2d<half> t0 [[id(11)]]; constant float4 *p [[id(12)]]; texture3d<float> t3 [[id(13)]];
            array<texture2d<float>, 3> arr [[id(14)]]; constant Inner &in [[id(20)]]; uint k [[id(21)]];
            float4 v [[id(22)]]; constant float *q [[id(23)]]; };
kernel void kab(constant AB &ab [[buffer(19)]], device float4 *out [[buffer(1)]], uint gid [[thread_position_in_grid]])
{ out[gid] = ab.p[0] + ab.in.b[0] + float4(ab.k) + ab.v + ab.q[0] + float4(ab.t0.read(uint2(0)))
            + ab.t3.read(uint3(0)) + ab.arr[gid & 1].read(uint2(0)) + ab.in.a.read(uint2(0)); }
