; Quad XOR for valid masks 0..3; all components travel as integer bits.
; MSL 2026-06-04 section 6.10.3: source and destination must be active.
target triple = "gpu_64-applecl-macosx"
define <3 x half> @air_quad_shuffle_xor_v3f16(<3 x half> %data, i16 %mask) alwaysinline nounwind convergent {
  %lane = call i32 @llvm.igil.get.simd.id()
  %m = zext i16 %mask to i32
  %source = xor i32 %lane, %m
  %bits = bitcast <3 x half> %data to <3 x i16>
  %b0 = extractelement <3 x i16> %bits, i32 0
  %b1 = extractelement <3 x i16> %bits, i32 1
  %b2 = extractelement <3 x i16> %bits, i32 2
  %w0 = zext i16 %b0 to i32
  %w1 = zext i16 %b1 to i32
  %w2 = zext i16 %b2 to i32
  %s0 = call i32 @llvm.igil.simd.shuffle(i32 %w0, i32 %source)
  %s1 = call i32 @llvm.igil.simd.shuffle(i32 %w1, i32 %source)
  %s2 = call i32 @llvm.igil.simd.shuffle(i32 %w2, i32 %source)
  %n0 = trunc i32 %s0 to i16
  %n1 = trunc i32 %s1 to i16
  %n2 = trunc i32 %s2 to i16
  %v0 = insertelement <3 x i16> undef, i16 %n0, i32 0
  %v1 = insertelement <3 x i16> %v0, i16 %n1, i32 1
  %v2 = insertelement <3 x i16> %v1, i16 %n2, i32 2
  %out = bitcast <3 x i16> %v2 to <3 x half>
  ret <3 x half> %out
}
declare i32 @llvm.igil.get.simd.id() nounwind readnone
declare i32 @llvm.igil.simd.shuffle(i32, i32) nounwind readnone convergent
