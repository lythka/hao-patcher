; Quad-local bit transport; MSL 2026-06-04 section 6.10.3.
; Valid selectors are 0..3, uniform per quad; the source lane must be active.
target triple = "gpu_64-applecl-macosx"
define <4 x half> @air_quad_shuffle_xor_v4f16(<4 x half> %data, i16 %selector) alwaysinline nounwind convergent {
  %lane = call i32 @llvm.igil.get.simd.id()
  %index = zext i16 %selector to i32
  %source = xor i32 %lane, %index
  %bits = bitcast <4 x half> %data to <4 x i16>
  %b0 = extractelement <4 x i16> %bits, i32 0
  %w0 = zext i16 %b0 to i32
  %s0 = call i32 @llvm.igil.simd.shuffle(i32 %w0, i32 %source)
  %n0 = trunc i32 %s0 to i16
  %v0 = insertelement <4 x i16> undef, i16 %n0, i32 0
  %b1 = extractelement <4 x i16> %bits, i32 1
  %w1 = zext i16 %b1 to i32
  %s1 = call i32 @llvm.igil.simd.shuffle(i32 %w1, i32 %source)
  %n1 = trunc i32 %s1 to i16
  %v1 = insertelement <4 x i16> %v0, i16 %n1, i32 1
  %b2 = extractelement <4 x i16> %bits, i32 2
  %w2 = zext i16 %b2 to i32
  %s2 = call i32 @llvm.igil.simd.shuffle(i32 %w2, i32 %source)
  %n2 = trunc i32 %s2 to i16
  %v2 = insertelement <4 x i16> %v1, i16 %n2, i32 2
  %b3 = extractelement <4 x i16> %bits, i32 3
  %w3 = zext i16 %b3 to i32
  %s3 = call i32 @llvm.igil.simd.shuffle(i32 %w3, i32 %source)
  %n3 = trunc i32 %s3 to i16
  %v3 = insertelement <4 x i16> %v2, i16 %n3, i32 3
  %out = bitcast <4 x i16> %v3 to <4 x half>
  ret <4 x half> %out
}
declare i32 @llvm.igil.get.simd.id() nounwind readnone
declare i32 @llvm.igil.simd.shuffle(i32, i32) nounwind readnone convergent
