; Newly authored runtime helper. Float values are shuffled as unchanged bits.
target triple = "gpu_64-applecl-macosx"

define float @air_simd_shuffle_rotate_down_f32(float %data, i16 %delta) alwaysinline nounwind {
entry:
  %lane = call i32 @llvm.igil.get.simd.id()
  %width = call i32 @llvm.igil.get.simd.size()
  %distance = zext i16 %delta to i32
  %sum = add i32 %lane, %distance
  %source = urem i32 %sum, %width
  %bits = bitcast float %data to i32
  %shuffled = call i32 @llvm.igil.simd.shuffle(i32 %bits, i32 %source)
  %result = bitcast i32 %shuffled to float
  ret float %result
}

declare i32 @llvm.igil.get.simd.id() nounwind readnone
declare i32 @llvm.igil.get.simd.size() nounwind readnone
declare i32 @llvm.igil.simd.shuffle(i32, i32) nounwind readnone
