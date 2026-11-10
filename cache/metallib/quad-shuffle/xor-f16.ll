; Quad-local bit transport; MSL 2026-06-04 section 6.10.3.
; Valid selectors are 0..3, uniform per quad; the source lane must be active.
target triple = "gpu_64-applecl-macosx"
define half @air_quad_shuffle_xor_f16(half %data, i16 %selector) alwaysinline nounwind convergent {
  %lane = call i32 @llvm.igil.get.simd.id()
  %index = zext i16 %selector to i32
  %source = xor i32 %lane, %index
  %bits = bitcast half %data to i16
  %w0 = zext i16 %bits to i32
  %s0 = call i32 @llvm.igil.simd.shuffle(i32 %w0, i32 %source)
  %n0 = trunc i32 %s0 to i16
  %out = bitcast i16 %n0 to half
  ret half %out
}
declare i32 @llvm.igil.get.simd.id() nounwind readnone
declare i32 @llvm.igil.simd.shuffle(i32, i32) nounwind readnone convergent
