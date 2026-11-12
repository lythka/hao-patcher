#include <metal_stdlib>
using namespace metal;
struct A1 { texture2d<float> t [[id(0)]]; device float4 *b [[id(1)]]; };
kernel void tA(constant A1 &ab [[buffer(0)]], device float4 *out [[buffer(1)]], uint gid [[thread_position_in_grid]])
{ out[gid] = ab.t.read(uint2(0, 0)) + ab.b[0]; }
struct A2 { device float4 *p [[id(0)]]; device float4 *q [[id(1)]]; };
kernel void tB(constant A2 &ab [[buffer(0)]], device float4 *out [[buffer(1)]], uint gid [[thread_position_in_grid]])
{ out[gid] = ab.p[gid & 3] + ab.q[1]; }
struct Inner { device float4 *b [[id(0)]]; texture2d<float> a [[id(1)]]; };
struct Outer { constant Inner &in [[id(0)]]; texture2d<float> t [[id(1)]]; };
kernel void tC(constant Outer &ab [[buffer(0)]], device float4 *out [[buffer(1)]], uint gid [[thread_position_in_grid]])
{ out[gid] = ab.in.b[gid & 3] + ab.in.a.read(uint2(0, 0)) + ab.t.read(uint2(0, 0)); }
struct A4 { uint k [[id(0)]]; device float4 *p [[id(1)]]; float4 v [[id(2)]]; };
kernel void tD(constant A4 &ab [[buffer(0)]], constant A1 &ab2 [[buffer(2)]], device float4 *out [[buffer(1)]],
               uint gid [[thread_position_in_grid]])
{ out[gid] = float4(ab.k) + ab.p[0] + ab.v + ab2.t.read(uint2(0, 0)) + ab2.b[1]; }
