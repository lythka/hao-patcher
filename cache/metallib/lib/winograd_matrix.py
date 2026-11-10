"""Lower MPS Winograd's Apple matrix operations using its existing shared tiles.

The caller selects the MPS Winograd family, whose workgroup X dimension is 32.
Use local X as its logical Apple lane, independent of Intel's physical SIMD
width. Each operand must be a shared vector load (optionally half-to-float).
Trace the pointer's loop-carried constant strides to its lane-dependent offset;
clone only that integer expression at the source lane and load the neighboring
scalar from the same synchronized tile. Existing workgroup barriers stay.

The 4x4/8x8 layouts and the ordered float/half FMAs were measured against Apple
M4 Max hardware using exhaustive basis products and randomized input matrices.
This is not a general lowering for arbitrary matrix operands or workgroup shapes.
"""
import re
import null_compare

def kernel_bindings(text, functions, calls):
    """Follow the two AIR builtin arguments through direct internal calls."""
    nodes = dict(re.findall('^!(\\d+) = !\\{(.*)\\}$', text, re.M))
    roots = re.search('^!air.kernel = !\\{(.*)\\}$', text, re.M)
    bindings = {}
    if not roots:
        raise ValueError('matrix module has no kernel metadata')
    for root in re.findall('!(\\d+)', roots[1]):
        fields = null_compare.split_top(nodes[root])
        name = re.search('@("[^"]+"|[^, ]+)$', fields[0])[1]
        arguments = null_compare.split_top(nodes[fields[2].lstrip('!')])
        bound = {}
        for reference in arguments:
            arg = nodes[reference.lstrip('!')]
            for semantic, key in [('air.thread_index_in_simdgroup', 'lane'), ('air.thread_position_in_threadgroup', 'local')]:
                if '"' + semantic + '"' in arg:
                    index = int(re.match('i32 (\\d+),', arg)[1])
                    bound[key] = functions[name]['args'][index].split()[-1]
        if bound:
            bindings[name] = bound
    changed = True
    while changed:
        changed = False
        for callee, rows in calls.items():
            if callee not in functions:
                continue
            for caller, _, args in rows:
                for key, value in list(bindings.get(caller, {}).items()):
                    for index, arg in enumerate(args):
                        if arg.split()[-1] != value:
                            continue
                        target = functions[callee]['args'][index].split()[-1]
                        bound = bindings.setdefault(callee, {})
                        if key in bound and bound[key] != target:
                            raise ValueError('ambiguous matrix builtin: ' + callee)
                        if key not in bound:
                            bound[key] = target
                            changed = True
    return bindings
V = re.compile('%[-\\w.$]+')

def rewrite(text):
    if 'llvm.agx2.' not in text:
        return (text, dict(calls=0))
    lines = text.splitlines()
    fs, calls = null_compare.read_functions(lines)
    if not any(re.fullmatch(r'llvm\.agx2\.f(16|32)matmad(4|8)x\2\.v2f\1', name)
               for name in calls):
        return text, dict(calls=0)
    if re.search(r'%(?:sharedmatrix\d+\.|matrix\.(?:local_x|logical_lane))', text):
        raise ValueError('matrix temporary names already in use')
    original = list(lines)
    bindings = kernel_bindings(text, fs, calls)
    count = 0
    used = set()
    rebound = set()
    for intrinsic, rows in calls.items():
        m = re.fullmatch('llvm.agx2.f(16|32)matmad(4|8)x\\2.v2f\\1', intrinsic)
        if not m:
            continue
        ty = 'float' if m[1] == '32' else 'half'
        suffix = 'f' + m[1]
        size = int(m[2])
        vec = f'<2 x {ty}>'
        used.add(suffix)
        for fn, i, args in rows:
            f = fs[fn]
            bound = bindings.get(fn, {})
            assert set(bound) == {'lane', 'local'}, ('missing matrix builtins', fn)
            lane = bound['lane']
            lid = bound['local']
            defs = {d[1]: d[2] for l in original[f['header'] + 1:f['end']] if (d := re.match('^  (%[-\\w.$]+) = (.*)$', l))}

            def depends(v, seen=()):
                if v == lane:
                    return True
                if v in seen:
                    return False
                return any((depends(x, (*seen, v)) for x in V.findall(defs.get(v, ''))))

            def pointer_offsets(ptr, memty, seen=None):
                if seen is None:
                    seen = set()
                if ptr in seen:
                    return set()
                seen.add(ptr)
                rhs = defs.get(ptr, '')
                out = set()
                if rhs.startswith('getelementptr '):
                    parts = null_compare.split_top(re.sub('^getelementptr (?:inbounds )?', '', rhs))
                    assert len(parts) == 3, (ptr, parts)
                    base = parts[1].split()[-1]
                    idx = parts[2].split()[-1]
                    if depends(idx):
                        assert parts[2].startswith('i64 '), (ptr, parts)
                        assert parts[0] in (memty, f'<2 x {memty}>'), (ptr, parts)
                        out.add((idx, 1 if parts[0] == memty else 2))
                    else:
                        assert re.fullmatch(r'-?\d+', idx), ('nonconstant shared stride', ptr, idx)
                    out |= pointer_offsets(base, memty, seen)
                elif rhs.startswith('bitcast '):
                    base = re.search(' (%[-\\w.$]+) to ', rhs)[1]
                    out |= pointer_offsets(base, memty, seen)
                elif rhs.startswith('phi '):
                    for v in re.findall('\\[ (%[-\\w.$]+), %[-\\w.$]+ \\]', rhs):
                        out |= pointer_offsets(v, memty, seen)
                elif ptr not in {a.split()[-1] for a in f['args']}:
                    raise ValueError('unproven pointer ' + ptr + ': ' + rhs)
                return out
            abc = [a.split()[-1] for a in args]
            pointer = []
            for v in abc[:2]:
                memty = ty
                convert = re.fullmatch('tail call fast <2 x float> @air.convert.f.v2f32.f.v2f16\\(<2 x half> (%[-\\w.$]+)\\).*', defs.get(v, ''))
                if convert:
                    v = convert[1]
                    memty = 'half'
                memvec = f'<2 x {memty}>'
                l = re.fullmatch('load ' + re.escape(memvec) + ', ' + re.escape(memvec) + ' addrspace\\(3\\)\\* (%[-\\w.$]+), align \\d+.*', defs.get(v, ''))
                assert l, (v, defs.get(v, ''))
                p = l[1]
                offsets = pointer_offsets(p, memty)
                assert len(offsets) == 1, (p, offsets)
                pointer.append((p, *next(iter(offsets)), memty))
            out = []
            prefix = f'sharedmatrix{count}.'

            def emit(n, expr):
                value = '%' + prefix + n
                out.append('  ' + value + ' = ' + expr)
                return value

            def offset_at(v, newlane, tag, cache):
                if v == lane:
                    return newlane
                if not depends(v):
                    return v
                if v in cache:
                    return cache[v]
                rhs = defs[v]
                assert re.match('(?:zext|sext|trunc|and|or|shl|lshr|add|mul) ', rhs), (v, rhs)
                rhs = V.sub(lambda match: offset_at(match[0], newlane, tag, cache), rhs)
                cache[v] = emit(tag + v[1:], rhs)
                return cache[v]

            def fetch(which, newlane, component, tag):
                p, index, scale, memty = pointer[which]
                memvec = f'<2 x {memty}>'
                newindex = offset_at(index, newlane, tag, {})
                diff = emit(tag + 'delta', f'sub i64 {newindex}, {index}')
                if scale != 1:
                    diff = emit(tag + 'scaled', f'mul i64 {diff}, {scale}')
                if component:
                    diff = emit(tag + 'component', f'add i64 {diff}, {component}')
                scalar = emit(tag + 'base', f'bitcast {memvec} addrspace(3)* {p} to {memty} addrspace(3)*')
                addr = emit(tag + 'addr', f'getelementptr {memty}, {memty} addrspace(3)* {scalar}, i64 {diff}')
                value = emit(tag + 'value', f"load {memty}, {memty} addrspace(3)* {addr}, align {(4 if memty == 'float' else 2)}")
                return emit(tag + 'converted', f'fpext half {value} to float') if memty != ty else value
            arow = emit('arow', f'and i16 {lane}, {(-2 if size == 4 else 22)}')
            bcol = emit('bcol', f'and i16 {lane}, {(-7 if size == 4 else 9)}')
            acc = [emit(f'c{c}', f'extractelement {vec} {abc[2]}, i32 {c}') for c in range(2)]
            for k in range(size):
                ai = emit(f'ai{k}', f'or i16 {arow}, {(k & 3) // 2 + (k & 4) * 2}')
                bi = emit(f'bi{k}', f'or i16 {bcol}, {(k & 3) * 2 + (k & 4) * 4}')
                a = fetch(0, ai, k & 1, f'a{k}.')
                for c in range(2):
                    b = fetch(1, bi, c, f'b{k}_{c}.')
                    acc[c] = emit(f'acc{k}_{c}', f'call {ty} @air.fma.{suffix}({ty} {a}, {ty} {b}, {ty} {acc[c]})')
            r = emit('result', f'insertelement {vec} undef, {ty} {acc[0]}, i32 0')
            result = original[i].split('=')[0].strip()
            out.append(f'  {result} = insertelement {vec} {r}, {ty} {acc[1]}, i32 1')
            lines[i] = '\n'.join(out)
            count += 1
            rebound.add((fn, lane, lid))
    for fn, lane, lid in sorted(rebound):
        f = fs[fn]
        for j in range(f['header'] + 1, f['end']):
            lines[j] = re.sub(re.escape(lane) + '(?![-\\w.$])', '%matrix.logical_lane', lines[j])
        lines[f['header']] += '\n  %matrix.local_x = extractelement <3 x i32> ' + lid + ', i32 0\n  %matrix.logical_lane = trunc i32 %matrix.local_x to i16'
    for suffix in sorted(used):
        ty = 'float' if suffix == 'f32' else 'half'
        if not any((l.startswith('declare ') and '@air.fma.' + suffix + '(' in l for l in lines)):
            lines.append(f'declare {ty} @air.fma.{suffix}({ty}, {ty}, {ty}) nounwind readnone')
    return ('\n'.join(lines) + '\n', dict(calls=count, functions=sorted((fn for fn, _, _ in rebound))))
