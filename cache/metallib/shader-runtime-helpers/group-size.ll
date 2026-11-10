; Query the selected Intel SIMD execution width; do not hardcode a lane count.
target triple = "gpu_64-applecl-macosx"

define i16 @air_get_simdgroup_size_i16() alwaysinline nounwind {
entry:
  %width = call i32 @llvm.igil.get.simd.size()
  %result = trunc i32 %width to i16
  ret i16 %result
}

declare i32 @llvm.igil.get.simd.size() nounwind readnone
