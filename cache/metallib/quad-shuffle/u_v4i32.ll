; HD 4000 quad_shuffle_down: keep the upper delta lanes unchanged.
; Metal Shading Language 2026-06-04, section 6.10.3, page 229.
target triple = "gpu_64-applecl-macosx"
define <4 x i32> @air_quad_shuffle_down_u_v4i32(<4 x i32> %data, i16 %delta) alwaysinline nounwind convergent {
entry:
  %lane = call i32 @llvm.igil.get.simd.id()
  %quad = and i32 %lane, 3
  %d = zext i16 %delta to i32
  %qsource = add i32 %quad, %d
  %inside = icmp ult i32 %qsource, 4
  %shifted = add i32 %lane, %d
  %source = select i1 %inside, i32 %shifted, i32 %lane
  %v0 = extractelement <4 x i32> %data, i32 0
  %s0 = call i32 @llvm.igil.simd.shuffle(i32 %v0, i32 %source)
  %r0 = insertelement <4 x i32> undef, i32 %s0, i32 0
  %v1 = extractelement <4 x i32> %data, i32 1
  %s1 = call i32 @llvm.igil.simd.shuffle(i32 %v1, i32 %source)
  %r1 = insertelement <4 x i32> %r0, i32 %s1, i32 1
  %v2 = extractelement <4 x i32> %data, i32 2
  %s2 = call i32 @llvm.igil.simd.shuffle(i32 %v2, i32 %source)
  %r2 = insertelement <4 x i32> %r1, i32 %s2, i32 2
  %v3 = extractelement <4 x i32> %data, i32 3
  %s3 = call i32 @llvm.igil.simd.shuffle(i32 %v3, i32 %source)
  %r3 = insertelement <4 x i32> %r2, i32 %s3, i32 3
  ret <4 x i32> %r3
}
declare i32 @llvm.igil.get.simd.id() nounwind readnone
declare i32 @llvm.igil.simd.shuffle(i32, i32) nounwind readnone convergent
