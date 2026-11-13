#include <metal_stdlib>
using namespace metal;
// Render test shaders (metal-adapter-test render-color | render-texture). Positions come from a vertex buffer by
// vertex_id (no vertex descriptor); the constant colour from setFragmentBytes; the texture and its sampler from
// setFragmentTexture / setFragmentSamplerState.
struct VOut { float4 pos [[position]]; float2 uv; };

vertex VOut tri_vertex(device const float2 *pos [[buffer(0)]], uint vid [[vertex_id]]) {
  VOut o;
  o.pos = float4(pos[vid], 0.0f, 1.0f);
  o.uv = float2(pos[vid].x + 1.0f, 1.0f - pos[vid].y) * 0.5f;  // NDC -> texture coordinates, origin top left
  return o;
}

fragment float4 solid_fragment(VOut in [[stage_in]], constant float4 &color [[buffer(0)]]) { return color; }

fragment float4 tex_fragment(VOut in [[stage_in]], texture2d<float> tex [[texture(0)]], sampler s [[sampler(0)]]) {
  return tex.sample(s, in.uv);
}
