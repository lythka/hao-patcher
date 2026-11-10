; Valid input domain: offset + count <= 64. Zero count preserves base.
target triple = "gpu_64-applecl-macosx"
define i64 @air_insert_bits_u_i64(i64 %base, i64 %insert, i32 %offset, i32 %count) alwaysinline nounwind readnone {
entry:
  %empty = icmp eq i32 %count, 0
  br i1 %empty, label %unchanged, label %nonempty
unchanged:
  ret i64 %base
nonempty:
  %safe_count = and i32 %count, 63
  %count64 = zext i32 %safe_count to i64
  %one_above = shl i64 1, %count64
  %partial_mask = add i64 %one_above, -1
  %full = icmp eq i32 %count, 64
  %low_mask = select i1 %full, i64 -1, i64 %partial_mask
  %offset64 = zext i32 %offset to i64
  %mask = shl i64 %low_mask, %offset64
  %inverse = xor i64 %mask, -1
  %kept = and i64 %base, %inverse
  %positioned = shl i64 %insert, %offset64
  %new_bits = and i64 %positioned, %mask
  %result = or i64 %kept, %new_bits
  ret i64 %result
}
