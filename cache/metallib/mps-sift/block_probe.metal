// The multifetch kernel's block reads in isolation: the same texture (MPS's R16Snorm DoG array), bound
// sampler (pixel coordinates, clamp-to-zero, nearest, no mip, captured from MPSImageSIFT), shape 2x1,
// constant offsets (0,0..3) and literal mip 0 as its twelve air.block_read_texture_2d_array.v4f32 calls,
// at every coordinate in and around the texture. read() and sample() of the same texels are controls.
#include <metal_stdlib>
using namespace metal;
kernel void probe(texture2d_array<float> tex [[texture(0)]], sampler s [[sampler(0)]],
                  device const int4 *coords [[buffer(0)]], device float4 *out [[buffer(1)]],
                  uint tid [[thread_position_in_grid]]) {
  int4 p = coords[tid];
  uint layer = uint(p.z);
  float4 a, b;
  switch (p.w) {
  case 0: { auto r = tex.block_read<2, 1>(s, p.xy, layer, int2(0, 0), 0u); a = r.value<0, 0>(); b = r.value<1, 0>(); break; }
  case 1: { auto r = tex.block_read<2, 1>(s, p.xy, layer, int2(0, 1), 0u); a = r.value<0, 0>(); b = r.value<1, 0>(); break; }
  case 2: { auto r = tex.block_read<2, 1>(s, p.xy, layer, int2(0, 2), 0u); a = r.value<0, 0>(); b = r.value<1, 0>(); break; }
  default: { auto r = tex.block_read<2, 1>(s, p.xy, layer, int2(0, 3), 0u); a = r.value<0, 0>(); b = r.value<1, 0>(); break; }
  }
  int2 q = p.xy + int2(0, p.w);
  int w = int(tex.get_width()), h = int(tex.get_height());
  bool in0 = q.x >= 0 && q.x < w && q.y >= 0 && q.y < h, in1 = q.x + 1 >= 0 && q.x + 1 < w && q.y >= 0 && q.y < h;
  out[tid * 6 + 0] = a;
  out[tid * 6 + 1] = b;
  out[tid * 6 + 2] = in0 ? tex.read(uint2(q), layer) : float4(0);
  out[tid * 6 + 3] = in1 ? tex.read(uint2(q + int2(1, 0)), layer) : float4(0);
  out[tid * 6 + 4] = tex.sample(s, float2(q) + 0.5f, layer, level(0.0f));
  out[tid * 6 + 5] = tex.sample(s, float2(q + int2(1, 0)) + 0.5f, layer, level(0.0f));
}
