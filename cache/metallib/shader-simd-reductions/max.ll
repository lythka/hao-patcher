target triple = "gpu_64-applecl-macosx"
define float @air_simd_max_f32(float %value) alwaysinline nounwind convergent {
entry:
  %active = call i64 @llvm.igil.active.channel.mask()
  %bits = bitcast float %value to i32
  br label %loop
loop:
  %mask = phi i64 [ %active, %entry ], [ %rest, %next ]
  %lane = phi i32 [ 0, %entry ], [ %lane_next, %next ]
  %acc = phi float [ %value, %entry ], [ %merged, %next ]
  %bit = and i64 %mask, 1
  %participates = icmp ne i64 %bit, 0
  br i1 %participates, label %read_lane, label %next
read_lane:
  %other_bits = call i32 @llvm.igil.simd.shuffle(i32 %bits, i32 %lane)
  %other = bitcast i32 %other_bits to float
  %reduced = call float @llvm.igil.maxf(float %acc, float %other)
  br label %next
next:
  %merged = phi float [ %acc, %loop ], [ %reduced, %read_lane ]
  %rest = lshr i64 %mask, 1
  %lane_next = add i32 %lane, 1
  %more = icmp ne i64 %rest, 0
  br i1 %more, label %loop, label %done
done:
  ret float %merged
}
declare i64 @llvm.igil.active.channel.mask() nounwind convergent
declare i32 @llvm.igil.simd.shuffle(i32, i32) nounwind readnone convergent
declare float @llvm.igil.maxf(float, float) nounwind readnone
