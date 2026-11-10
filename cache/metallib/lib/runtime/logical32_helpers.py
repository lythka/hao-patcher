"""Shared exchange IR for the runtime pass and its original diagnostic reference."""
NEUTRAL = "0xC7EFFFFFE0000000"  # -FLT_MAX

def helpers(modern):
    barrier = 'call void @air.wg.barrier(i32 2, ' + ('i32 5, ' if modern else '') + 'i32 1)'
    out = ['@logical32_words = internal addrspace(3) global [32 x i32] undef, align 4',
           '@logical32_vectors = internal addrspace(3) global [32 x <4 x float>] undef, align 16',
           'declare void @air.wg.barrier(i32, ' + ('i32, ' if modern else '') + 'i32) convergent nounwind']
    for name, ty in [('broadcast_i32', 'i32'), ('shuffle_v4f32', '<4 x float>')]:
        array = 'logical32_words' if ty == 'i32' else 'logical32_vectors'
        out += [f'define internal {ty} @logical32_{name}({ty} %v, i16 %source, i16 %self) alwaysinline convergent nounwind {{',
                'entry:',
                f' %p = getelementptr [32 x {ty}], [32 x {ty}] addrspace(3)* @{array}, i32 0, i16 %self',
                f' store {ty} %v, {ty} addrspace(3)* %p', f' {barrier}']
        source = '%source'
        if name.startswith('shuffle'):
            # Lane 31's neighbor result is not consumed by this shader's detection.
            out += [' %sum = add i16 %self, %source', ' %index = and i16 %sum, 31']
            source = '%index'
        out += [f' %q = getelementptr [32 x {ty}], [32 x {ty}] addrspace(3)* @{array}, i32 0, i16 {source}',
                f' %r = load {ty}, {ty} addrspace(3)* %q', f' {barrier}', f' ret {ty} %r', '}']
    out += ['define internal float @logical32_broadcast_f32(float %v, i16 %source, i16 %self) alwaysinline convergent nounwind {',
            ' %bits = bitcast float %v to i32',
            ' %r = call i32 @logical32_broadcast_i32(i32 %bits, i16 %source, i16 %self)',
            ' %f = bitcast i32 %r to float', ' ret float %f', '}']
    for name in ('ballot', 'max'):
        ty, argty = ('i64', 'i1') if name == 'ballot' else ('float', 'float')
        out += [f'define internal {ty} @logical32_{name}({argty} %v, i16 %self) alwaysinline convergent nounwind {{',
                'entry:',
                ' %p = getelementptr [32 x i32], [32 x i32] addrspace(3)* @logical32_words, i32 0, i16 %self']
        if name == 'ballot':
            out += [' %value = zext i1 %v to i32', ' %lane = zext i16 %self to i32',
                    ' %bits = shl i32 %value, %lane']
        else:
            out += [' %bits = bitcast float %v to i32']
        out += [' store i32 %bits, i32 addrspace(3)* %p', f' {barrier}', ' br label %loop',
                'loop:', ' %i = phi i32 [0, %entry], [%next, %loop]']
        out += [' %acc = phi i32 [0, %entry], [%r, %loop]' if name == 'ballot' else
                f' %acc = phi float [{NEUTRAL}, %entry], [%r, %loop]']
        out += [' %q = getelementptr [32 x i32], [32 x i32] addrspace(3)* @logical32_words, i32 0, i32 %i',
                ' %b = load i32, i32 addrspace(3)* %q']
        if name == 'ballot':
            out += [' %r = or i32 %acc, %b']
        else:
            out += [' %f = bitcast i32 %b to float',
                    ' %r = call float @air.fast_fmax.f32(float %acc, float %f)']
        out += [' %next = add i32 %i, 1', ' %more = icmp ult i32 %next, 32',
                ' br i1 %more, label %loop, label %done', 'done:', f' {barrier}']
        out += [' %wide = zext i32 %r to i64', ' ret i64 %wide'] if name == 'ballot' else [' ret float %r']
        out += ['}']
    return '\n'.join(out) + '\n'
