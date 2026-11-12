#include <metal_stdlib>
using namespace metal;
struct AS { texture2d<float> t [[id(0)]]; sampler s [[id(1)]]; };
constexpr sampler cs(filter::linear);
// c3: AB tex + AB sampler, plus ordinary texture sampled with a constexpr sampler
kernel void c3(texture2d<float> tx [[texture(2)]], constant AS &ab [[buffer(0)]], device float4 *out [[buffer(1)]], uint gid [[thread_position_in_grid]])
{ out[gid] = tx.sample(cs, float2(0.5), level(0)) + ab.t.sample(ab.s, float2(0.5), level(0)); }
// c4: ordinary texture + AB sampler
kernel void c4(texture2d<float> tx [[texture(2)]], constant AS &ab [[buffer(0)]], device float4 *out [[buffer(1)]], uint gid [[thread_position_in_grid]])
{ out[gid] = tx.sample(ab.s, float2(0.5), level(0)); }
// c6: AB tex + AB sampler, ordinary sampler argument declared but used only with the AB texture? no: unused
kernel void c6(sampler sm [[sampler(3)]], constant AS &ab [[buffer(0)]], device float4 *out [[buffer(1)]], uint gid [[thread_position_in_grid]])
{ out[gid] = ab.t.sample(ab.s, float2(0.5), level(0)); }
// c7: AB tex + AB sampler, ordinary sampler used with an ordinary texture (like kp, one each)
kernel void c7(texture2d<float> tx [[texture(2)]], sampler sm [[sampler(3)]], constant AS &ab [[buffer(0)]], device float4 *out [[buffer(1)]], uint gid [[thread_position_in_grid]])
{ out[gid] = ab.t.sample(ab.s, float2(0.5), level(0)) + tx.sample(sm, float2(0.5), level(0)); }
// c8: two AB samples, two AB samplers, no ordinary sampler (control)
kernel void c8(constant AS &ab [[buffer(0)]], constant AS &ab2 [[buffer(2)]], device float4 *out [[buffer(1)]], uint gid [[thread_position_in_grid]])
{ out[gid] = ab.t.sample(ab.s, float2(0.5), level(0)) + ab2.t.sample(ab2.s, float2(0.5), level(0)); }
