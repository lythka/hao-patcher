"""Legacy Intel implementations of observed Metal SIMD/quad collectives.

Walk the captured active mask in lane order. All active lanes execute each
shuffle together; prefix/quad membership selects the result *after* the shuffle.
This matters under divergence: branching on a per-lane prefix before shuffling
would remove the source lanes. Floating sums use an explicit ordered fold.
"""
import re


def scalar_type(suffix):
    return {'f32': 'float', 'f16': 'half', 'u_i32': 'i32', 's_i32': 'i32',
            'u_i16': 'i16', 'u_i8': 'i8', 'bool': 'i1'}[suffix]


def reduction(symbol, op, suffix, scope='simd', prefix=None):
    match = re.fullmatch(r'v([234])(f(?:16|32))', suffix)
    count, scalar = (int(match[1]), scalar_type(match[2])) if match else (1, scalar_type(suffix))
    ty = f'<{count} x {scalar}>' if count > 1 else scalar
    floating = scalar in ('half', 'float')
    zero = 'zeroinitializer' if count > 1 else '0.0' if floating else '0'
    # IEEE addition's sign-preserving identity is -0; Apple's exclusive scan
    # returns it for the first active lane, including a singleton sparse group.
    if floating:
        element = '0xH8000' if scalar == 'half' else '-0.0'
        zero = '<' + ', '.join(f'{scalar} {element}' for _ in range(count)) + '>' if count > 1 else element
    initial = '%value' if op in ('min', 'max') else 'true' if op == 'all' else zero
    lines = [f'define {ty} @{symbol}({ty} %value) alwaysinline nounwind convergent {{',
             'entry:', '  %active32 = call i32 @llvm.igil.active.channel.mask()',
             '  %active = zext i32 %active32 to i64',
             '  %self = call i32 @llvm.igil.get.simd.id()', '  br label %loop', 'loop:',
             '  %mask = phi i64 [ %active, %entry ], [ %rest, %next ]',
             '  %lane = phi i32 [ 0, %entry ], [ %nextlane, %next ]',
             f'  %acc = phi {ty} [ {initial}, %entry ], [ %merged, %next ]',
             '  %bit = and i64 %mask, 1', '  %present = icmp ne i64 %bit, 0',
             '  br i1 %present, label %read, label %next', 'read:']
    for i in range(count):
        value = '%value'
        if count > 1:
            value = f'%v{i}'
            lines.append(f'  {value} = extractelement {ty} %value, i32 {i}')
        if floating:
            bits = 16 if scalar == 'half' else 32
            lines.append(f'  %bits{i} = bitcast {scalar} {value} to i{bits}')
            value = f'%bits{i}'
        else:
            bits = int(scalar[1:])
        if bits < 32:
            lines.append(f'  %wide{i} = zext i{bits} {value} to i32')
            value = f'%wide{i}'
        lines.append(f'  %shuffle{i} = call i32 @llvm.igil.simd.shuffle(i32 {value}, i32 %lane)')
        value = f'%shuffle{i}'
        if bits < 32:
            lines.append(f'  %narrow{i} = trunc i32 {value} to i{bits}')
            value = f'%narrow{i}'
        if floating:
            lines.append(f'  %float{i} = bitcast i{bits} {value} to {scalar}')
            value = f'%float{i}'
        if count > 1:
            previous = 'undef' if i == 0 else f'%vector{i-1}'
            lines.append(f'  %vector{i} = insertelement {ty} {previous}, {scalar} {value}, i32 {i}')
            value = f'%vector{i}'
    if op in ('min', 'max'):
        if floating:
            if count > 1:
                for i in range(count):
                    lines += [f'  %a{i} = extractelement {ty} %acc, i32 {i}',
                              f'  %b{i} = extractelement {ty} {value}, i32 {i}']
                    a, b = f'%a{i}', f'%b{i}'
                    if scalar == 'half':
                        lines += [f'  %af{i} = fpext half {a} to float',
                                  f'  %bf{i} = fpext half {b} to float']
                        a, b = f'%af{i}', f'%bf{i}'
                    lines.append(f'  %m{i} = call float @llvm.igil.{op}f(float {a}, float {b})')
                    v = f'%m{i}'
                    if scalar == 'half':
                        lines.append(f'  %mh{i} = fptrunc float {v} to half')
                        v = f'%mh{i}'
                    previous = 'undef' if i == 0 else f'%minmax{i-1}'
                    result = '%reduced' if i == count-1 else f'%minmax{i}'
                    lines.append(f'  {result} = insertelement {ty} {previous}, {scalar} {v}, i32 {i}')
            elif scalar == 'half':
                lines += [f'  %a = fpext half %acc to float', f'  %b = fpext half {value} to float',
                          f'  %m = call float @llvm.igil.{op}f(float %a, float %b)',
                          '  %reduced = fptrunc float %m to half']
            else:
                lines.append(f'  %reduced = call float @llvm.igil.{op}f(float %acc, float {value})')
        else:
            pred = 'ult' if op == 'min' else 'ugt'
            lines += [f'  %better = icmp {pred} {ty} {value}, %acc',
                      f'  %reduced = select i1 %better, {ty} {value}, {ty} %acc']
    else:
        inst = {'sum': 'fadd' if floating else 'add', 'or': 'or', 'any': 'or', 'all': 'and'}[op]
        lines.append(f'  %reduced = {inst} {ty} %acc, {value}')
    if scope == 'quad':
        lines += ['  %different = xor i32 %lane, %self', '  %otherquad = and i32 %different, -4',
                  '  %include = icmp eq i32 %otherquad, 0']
    elif prefix:
        pred = 'ult' if prefix == 'exclusive' else 'ule'
        lines.append(f'  %include = icmp {pred} i32 %lane, %self')
    else:
        lines.append('  %include = icmp ne i64 %active, 0')
    lines += [f'  %selected = select i1 %include, {ty} %reduced, {ty} %acc',
              '  br label %next', 'next:',
              f'  %merged = phi {ty} [ %acc, %loop ], [ %selected, %read ]',
              '  %rest = lshr i64 %mask, 1', '  %nextlane = add i32 %lane, 1',
              '  %more = icmp ne i64 %rest, 0', '  br i1 %more, label %loop, label %done',
              'done:', f'  ret {ty} %merged', '}']
    declarations = ['declare i32 @llvm.igil.active.channel.mask() nounwind convergent',
                    'declare i32 @llvm.igil.get.simd.id() nounwind readnone',
                    'declare i32 @llvm.igil.simd.shuffle(i32, i32) nounwind readnone convergent']
    if floating and op in ('min', 'max'):
        declarations.append(f'declare float @llvm.igil.{op}f(float, float) nounwind readnone')
    return '\n'.join(lines + declarations) + '\n'


def sources():
    result = {}
    for suffix in ('f32', 'f16', 'v2f16', 'v3f32', 'v4f32', 'v4f16', 'u_i32', 's_i32'):
        symbol = 'air_simd_sum_' + suffix
        result[symbol] = reduction(symbol, 'sum', suffix)
    for op, suffix in [('min', 'f16'), ('max', 'f32'), ('min', 'f32'),
                       ('min', 'v3f32'), ('max', 'v3f32'),
                       ('min', 'u_i16'), ('max', 'u_i16'), ('or', 'u_i8')]:
        symbol = f'air_simd_{op}_{suffix}'
        result[symbol] = reduction(symbol, op, suffix)
    for scope in ('simd', 'quad'):
        for op in ('any', 'all'):
            symbol = f'air_{scope}_{op}'
            result[symbol] = reduction(symbol, op, 'bool', scope)
    for suffix in ('f16', 'v2f16', 'v3f16', 'v4f16', 'u_i32'):
        op = 'or' if suffix == 'u_i32' else 'sum'
        symbol = f'air_quad_{op}_{suffix}'
        result[symbol] = reduction(symbol, op, suffix, 'quad')
    for prefix, suffix in [('exclusive', 'f32'), ('exclusive', 'v4f32'), ('inclusive', 'u_i32')]:
        symbol = f'air_simd_prefix_{prefix}_sum_{suffix}'
        result[symbol] = reduction(symbol, 'sum', suffix, prefix=prefix)
    result['air_simd_is_first'] = '''define i1 @air_simd_is_first() alwaysinline nounwind convergent {
  %mask32 = call i32 @llvm.igil.active.channel.mask()
  %mask = zext i32 %mask32 to i64
  %lane = call i32 @llvm.igil.get.simd.id()
  %wide = zext i32 %lane to i64
  %bit = shl i64 1, %wide
  %lower = sub i64 %bit, 1
  %before = and i64 %mask, %lower
  %first = icmp eq i64 %before, 0
  ret i1 %first
}
declare i32 @llvm.igil.active.channel.mask() nounwind convergent
declare i32 @llvm.igil.get.simd.id() nounwind readnone
'''
    # Capture participation before inspecting the predicate. Branching on the
    # predicate would remove false lanes from the shuffle's execution mask.
    result['air_simd_ballot_i64'] = '''define i64 @air_simd_ballot_i64(i1 %value) alwaysinline nounwind convergent {
entry:
  %active = call i32 @llvm.igil.active.channel.mask()
  %predicate = zext i1 %value to i32
  br label %loop
loop:
  %mask = phi i32 [ %active, %entry ], [ %rest, %next ]
  %lane = phi i32 [ 0, %entry ], [ %nextlane, %next ]
  %acc = phi i64 [ 0, %entry ], [ %merged, %next ]
  %bit = and i32 %mask, 1
  %present = icmp ne i32 %bit, 0
  br i1 %present, label %read, label %next
read:
  %vote = call i32 @llvm.igil.simd.shuffle(i32 %predicate, i32 %lane)
  %widevote = zext i32 %vote to i64
  %widelane = zext i32 %lane to i64
  %votebit = shl i64 %widevote, %widelane
  %selected = or i64 %acc, %votebit
  br label %next
next:
  %merged = phi i64 [ %acc, %loop ], [ %selected, %read ]
  %rest = lshr i32 %mask, 1
  %nextlane = add i32 %lane, 1
  %more = icmp ne i32 %rest, 0
  br i1 %more, label %loop, label %done
done:
  ret i64 %merged
}
declare i32 @llvm.igil.active.channel.mask() nounwind convergent
declare i32 @llvm.igil.simd.shuffle(i32, i32) nounwind readnone convergent
'''
    return {name: 'target triple = "gpu_64-applecl-macosx"\n' + body for name, body in result.items()}
