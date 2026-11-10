#include <metal_stdlib>
using namespace metal;
kernel void probe(texture2d_array<float> tex [[texture(0)]],
 device const int4 *coords [[buffer(0)]], device float4 *out [[buffer(1)]],
 uint tid [[thread_position_in_grid]]) {
 constexpr sampler s(coord::pixel, address::ADDRESS, filter::nearest);
 int4 p=coords[tid];
 auto b=tex.block_read<WIDTH,HEIGHT>(s,p.xy,uint(p.z),int2(OFFSET_X,OFFSET_Y),uint(p.w));
 out[tid*2]=b.value<0,0>();
 out[tid*2+1]=b.value<SECOND_X,SECOND_Y>();
}
