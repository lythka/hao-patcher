#include <metal_stdlib>
using namespace metal;
// inline struct members of an argument buffer: offsets the front end uses, and nested argument ids
struct Inl { float3 a; float b; texture2d<float> t; uint c; };
struct AB { uint k [[id(0)]]; Inl s [[id(1)]]; float2 v [[id(10)]]; texture2d<float> u [[id(11)]]; };
kernel void kin(constant AB &ab [[buffer(0)]], device float4 *out [[buffer(1)]], uint gid [[thread_position_in_grid]])
{ out[gid] = float4(ab.k) + float4(ab.s.a, ab.s.b) + float4(ab.s.c) + float4(ab.v, 0, 0)
           + ab.s.t.read(uint2(0)) + ab.u.read(uint2(0)); }
