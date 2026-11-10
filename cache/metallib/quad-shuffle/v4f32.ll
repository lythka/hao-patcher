; HD 4000 quad_shuffle_down: keep the upper delta lanes unchanged.
; Metal Shading Language 2026-06-04, section 6.10.3, page 229.
target triple = "gpu_64-applecl-macosx"
define <4 x float> @air_quad_shuffle_down_v4f32(<4 x float> %data, i16 %delta) alwaysinline nounwind convergent {
entry:
  %lane = call i32 @llvm.igil.get.simd.id()
  %quad = and i32 %lane, 3
  %d = zext i16 %delta to i32
  %qsource = add i32 %quad, %d
  %inside = icmp ult i32 %qsource, 4
  %shifted = add i32 %lane, %d
  %source = select i1 %inside, i32 %shifted, i32 %lane
  %v0 = extractelement <4 x float> %data, i32 0
  %b0 = bitcast float %v0 to i32
  %s0 = call i32 @llvm.igil.simd.shuffle(i32 %b0, i32 %source)
  %f0 = bitcast i32 %s0 to float
  %r0 = insertelement <4 x float> undef, float %f0, i32 0
  %v1 = extractelement <4 x float> %data, i32 1
  %b1 = bitcast float %v1 to i32
  %s1 = call i32 @llvm.igil.simd.shuffle(i32 %b1, i32 %source)
  %f1 = bitcast i32 %s1 to float
  %r1 = insertelement <4 x float> %r0, float %f1, i32 1
  %v2 = extractelement <4 x float> %data, i32 2
  %b2 = bitcast float %v2 to i32
  %s2 = call i32 @llvm.igil.simd.shuffle(i32 %b2, i32 %source)
  %f2 = bitcast i32 %s2 to float
  %r2 = insertelement <4 x float> %r1, float %f2, i32 2
  %v3 = extractelement <4 x float> %data, i32 3
  %b3 = bitcast float %v3 to i32
  %s3 = call i32 @llvm.igil.simd.shuffle(i32 %b3, i32 %source)
  %f3 = bitcast i32 %s3 to float
  %r3 = insertelement <4 x float> %r2, float %f3, i32 3
  ret <4 x float> %r3
}
declare i32 @llvm.igil.get.simd.id() nounwind readnone
declare i32 @llvm.igil.simd.shuffle(i32, i32) nounwind readnone convergent
