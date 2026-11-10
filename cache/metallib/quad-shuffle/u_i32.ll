; HD 4000 quad_shuffle_down: keep the upper delta lanes unchanged.
; Metal Shading Language 2026-06-04, section 6.10.3, page 229.
target triple = "gpu_64-applecl-macosx"
define i32 @air_quad_shuffle_down_u_i32(i32 %data, i16 %delta) alwaysinline nounwind convergent {
entry:
  %lane = call i32 @llvm.igil.get.simd.id()
  %quad = and i32 %lane, 3
  %d = zext i16 %delta to i32
  %qsource = add i32 %quad, %d
  %inside = icmp ult i32 %qsource, 4
  %shifted = add i32 %lane, %d
  %source = select i1 %inside, i32 %shifted, i32 %lane
  %s0 = call i32 @llvm.igil.simd.shuffle(i32 %data, i32 %source)
  ret i32 %s0
}
declare i32 @llvm.igil.get.simd.id() nounwind readnone
declare i32 @llvm.igil.simd.shuffle(i32, i32) nounwind readnone convergent
