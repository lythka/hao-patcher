; Quad-local bit transport; MSL 2026-06-04 section 6.10.3.
; Valid selectors are 0..3, uniform per quad; the source lane must be active.
target triple = "gpu_64-applecl-macosx"
define <3 x float> @air_quad_broadcast_v3f32(<3 x float> %data, i16 %selector) alwaysinline nounwind convergent {
  %lane = call i32 @llvm.igil.get.simd.id()
  %index = zext i16 %selector to i32
  %base = and i32 %lane, -4
  %source = or i32 %base, %index
  %bits = bitcast <3 x float> %data to <3 x i32>
  %b0 = extractelement <3 x i32> %bits, i32 0
  %s0 = call i32 @llvm.igil.simd.shuffle(i32 %b0, i32 %source)
  %v0 = insertelement <3 x i32> undef, i32 %s0, i32 0
  %b1 = extractelement <3 x i32> %bits, i32 1
  %s1 = call i32 @llvm.igil.simd.shuffle(i32 %b1, i32 %source)
  %v1 = insertelement <3 x i32> %v0, i32 %s1, i32 1
  %b2 = extractelement <3 x i32> %bits, i32 2
  %s2 = call i32 @llvm.igil.simd.shuffle(i32 %b2, i32 %source)
  %v2 = insertelement <3 x i32> %v1, i32 %s2, i32 2
  %out = bitcast <3 x i32> %v2 to <3 x float>
  ret <3 x float> %out
}
declare i32 @llvm.igil.get.simd.id() nounwind readnone
declare i32 @llvm.igil.simd.shuffle(i32, i32) nounwind readnone convergent
