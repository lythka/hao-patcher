#include <metal_stdlib>
using namespace metal;
kernel void probe(texture2d<float> image [[texture(0)]],device const float2 *coords [[buffer(0)]],device float4 *out [[buffer(1)]],uint tid [[thread_position_in_grid]]) {
 constexpr sampler s(coord::pixel,filter::linear,address::clamp_to_edge);
 int2 offset=int2((tid&2),((tid>>1)&2));out[tid]=image.sample(s,coords[tid],offset);
}
