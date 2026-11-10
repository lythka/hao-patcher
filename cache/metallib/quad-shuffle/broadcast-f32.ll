; Quad-local broadcast; selector 0..3 is uniform and names an active source.
; MSL 2026-06-04 section 6.10.3. No floating-point arithmetic is performed.
target triple = "gpu_64-applecl-macosx"
define float @air_quad_broadcast_f32(float %data, i16 %selector) alwaysinline nounwind convergent {
  %lane = call i32 @llvm.igil.get.simd.id()
  %base = and i32 %lane, -4
  %index = zext i16 %selector to i32
  %source = or i32 %base, %index
  %bits = bitcast float %data to i32
  %shuffled = call i32 @llvm.igil.simd.shuffle(i32 %bits, i32 %source)
  %out = bitcast i32 %shuffled to float
  ret float %out
}
declare i32 @llvm.igil.get.simd.id() nounwind readnone
declare i32 @llvm.igil.simd.shuffle(i32, i32) nounwind readnone convergent
