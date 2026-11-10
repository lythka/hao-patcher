"""Diagnostic candidate for two-texel array block reads; NOT an image repair.

Metal's block_color is ordered X + W*Y. Preserve layer, mip level, access mode
and the original offset for each read; move only the second integer coordinate.
The observed MPSImage calls do not request sparse-residency outputs.

Apple GPU controls agree, but Intel's later downgrade strips the sampler and
offsets from these reads. Native outputs differ. golden_gate deliberately does
not call this pass. It remains here to reproduce and measure that failure.
"""
import re
import null_compare
from texture_coords import param_type

BLOCK = 'air.block_read_texture_2d_array.v4f32'
READ = 'air.read_texture_2d_array.v4f32'
TYPES = ['%struct._texture_2d_array_t addrspace(1)*',
         '%struct._sampler_t addrspace(2)*', '<2 x i32>', 'i32',
         '<2 x i32>', 'i32', 'i32', '<4 x float>*', 'i8*', '<2 x i32>']
RESULT = '{ <4 x float>, i8 }'


def rewrite(text):
    if '@' + BLOCK + '(' not in text:
        return text, dict(calls=0)
    lines, output, count = text.splitlines(), [], 0
    for line in lines:
        if '@' + BLOCK + '(' not in line:
            output.append(line)
            continue
        _, _, args = null_compare.parameters(line, BLOCK)
        types = ['<4 x float>*' if a.startswith('<4 x float>*') else param_type(a) for a in args]
        if types != TYPES:
            raise ValueError('unrecognized array block-read signature')
        if line.startswith('declare void @'):
            continue
        if not re.match(r'^\s+(?:tail )?call void @', line):
            raise ValueError('unrecognized array block-read use: ' + line)
        if null_compare.last_value(args[8]) != 'null':
            raise ValueError('sparse array block-read is not implemented')
        shape = re.fullmatch(r'<2 x i32> <i32 ([12]), i32 ([12])>', args[9])
        if not shape or int(shape[1])*int(shape[2]) != 2:
            raise ValueError('unrecognized array block-read shape: ' + args[9])
        stem = f'%compat_block{count}'
        while stem in text:
            stem += '_'
        dx, dy = (1, 0) if shape[1] == '2' else (0, 1)
        coord = args[2][len('<2 x i32> '):]
        destination = null_compare.last_value(args[7])
        for element in range(2):
            operands = args[:7].copy()
            if element:
                output.append(f'  {stem}_coord = add <2 x i32> {coord}, <i32 {dx}, i32 {dy}>')
                operands[2] = f'<2 x i32> {stem}_coord'
            output += [f'  {stem}_read{element} = call {RESULT} @{READ}({", ".join(operands)})',
                       f'  {stem}_value{element} = extractvalue {RESULT} {stem}_read{element}, 0',
                       f'  {stem}_out{element} = getelementptr <4 x float>, <4 x float>* {destination}, i32 {element}',
                       f'  store <4 x float> {stem}_value{element}, <4 x float>* {stem}_out{element}, align 1']
        count += 1
    if count and not re.search(r'^declare .*@' + re.escape(READ) + r'\(', text, re.M):
        output.append(f'declare {RESULT} @{READ}({", ".join(TYPES[:7])}) nounwind argmemonly readonly')
    return '\n'.join(output) + '\n', dict(calls=count)
