#include <metal_stdlib>
using namespace metal;
struct Inst { float4 a; uint matIndex; uint pad[9]; };          // 56 bytes like RealityKit's per-instance record
struct Mat  { float4 base; float4 emissive; float pad[64]; };   // 288 bytes
struct Tables { constant Inst *inst; uint pad[8]; constant Mat *mats; };   // pointers at byte 0 and 40
constant float lut[8] = { 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8 };       // program-scope constants: C0 inline
struct VOut { float4 pos [[position]]; uint id [[flat, user(locn1)]]; };
vertex VOut vmain(uint vid [[vertex_id]]) { VOut o; o.pos = float4(float(vid & 1), float(vid >> 1), 0, 1); o.id = vid; return o; }
fragment float4 fmain(VOut in [[stage_in]], constant Tables &t [[buffer(21)]], constant uint *sel [[buffer(28)]]) {
    uint i = sel[in.id & 3];
    uint m = t.inst[i].matIndex;
    return t.mats[m].base * lut[in.id & 7] + t.mats[m].emissive + lut[sel[(in.id + 1) & 3] & 7];
}
