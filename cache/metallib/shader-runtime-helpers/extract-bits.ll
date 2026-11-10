; Valid input domain: offset + count <= 64. Zero count returns zero.
target triple = "gpu_64-applecl-macosx"
define i64 @air_extract_bits_u_i64(i64 %value, i32 %offset, i32 %count) alwaysinline nounwind readnone {
entry:
  %empty = icmp eq i32 %count, 0
  br i1 %empty, label %zero, label %nonempty
zero:
  ret i64 0
nonempty:
  %safe_count = and i32 %count, 63
  %count64 = zext i32 %safe_count to i64
  %one_above = shl i64 1, %count64
  %partial_mask = add i64 %one_above, -1
  %full = icmp eq i32 %count, 64
  %mask = select i1 %full, i64 -1, i64 %partial_mask
  %offset64 = zext i32 %offset to i64
  %shifted = lshr i64 %value, %offset64
  %result = and i64 %shifted, %mask
  ret i64 %result
}
