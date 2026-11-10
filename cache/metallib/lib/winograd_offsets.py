"""Replace bounded dynamic compute-sample offsets with constant-offset samples.

The Intel coordinate pass assumes ConstantInt offset components and crashes
on variables. Prove each component's small value set from integer SSA, sample
those exact constant offsets, then select both the pixel and residency status.
This preserves the sampler, coordinates, array index, LOD and offset semantics;
it adds up to three extra readonly samples for MPS Winograd, or eight for
MetalFX dilateFlow's private nine-offset table. Unproven offsets refuse conversion.
"""
import re
import itertools
import null_compare

def choices(value, definitions, seen=()):
    if re.fullmatch('-?\\d+', value):
        return {int(value)}
    if value in seen:
        raise ValueError('cyclic value')
    rhs = definitions.get(value, '')
    seen = (*seen, value)
    m = re.fullmatch('and i(\\d+) [%\\w.]+, (\\d+)', rhs)
    if m:
        mask = int(m[2])
        bits = [i for i in range(int(m[1])) if mask & 1 << i]
        if len(bits) > 2:
            raise ValueError('too many offset choices')
        return {sum((1 << bit for j, bit in enumerate(bits) if v & 1 << j)) for v in range(1 << len(bits))}
    m = re.fullmatch('zext i\\d+ (%[\\w.]+) to i\\d+', rhs)
    if m:
        return choices(m[1], definitions, seen)
    m = re.fullmatch('or i\\d+ (%[\\w.]+), (\\d+)', rhs)
    if m:
        return {a | int(m[2]) for a in choices(m[1], definitions, seen)}
    raise ValueError('unproven scalar offset ' + value + ': ' + rhs)

def private_tables(lines):
    """Prove finite values for nonescaping, completely initialized offset arrays."""
    roots, pointers, stores, structural = {}, {}, {}, set()
    for i, line in enumerate(lines):
        m = re.match(r'  (%[\w.]+) = alloca \[(\d+) x <2 x i32>\],', line)
        if m:
            roots[m[1]] = int(m[2])
            stores[m[1]] = {}
            structural.add(i)
    for i, line in enumerate(lines):
        m = re.match(r'  (%[\w.]+) = getelementptr(?: inbounds)? \[(\d+) x <2 x i32>\], '
                     r'\[\d+ x <2 x i32>\]\* (%[\w.]+), i64 0, i64 ([%\w.]+)', line)
        if m and m[3] in roots and int(m[2]) == roots[m[3]]:
            pointers[m[1]] = (m[3], m[4])
            structural.add(i)
    allowed = set()
    for i, line in enumerate(lines):
        m = re.match(r'  store <2 x i32> (zeroinitializer|<i32 -?\d+, i32 -?\d+>), '
                     r'<2 x i32>\* (%[\w.]+),', line)
        if m and m[2] in pointers:
            root, index = pointers[m[2]]
            if not index.isdigit() or int(index) in stores[root]:
                raise ValueError('offset table has dynamic or repeated stores')
            pair = (0, 0) if m[1] == 'zeroinitializer' else tuple(map(int, re.findall(r'i32 (-?\d+)', m[1])))
            stores[root][int(index)] = pair
            allowed.add(i)
    result = {}
    for root, size in roots.items():
        if set(stores[root]) != set(range(size)):
            raise ValueError('offset table is not completely initialized')
        aliases = {root} | {p for p, (r, _) in pointers.items() if r == root}
        lifetime_aliases = set()
        for i, line in enumerate(lines):
            m = re.match(r'  (%[\w.]+) = bitcast \[\d+ x <2 x i32>\]\* '+re.escape(root)+r' to i8\*', line)
            if m:
                lifetime_aliases.add(m[1])
                structural.add(i)
        for i, line in enumerate(lines):
            refs = set(re.findall(r'%[\w.]+', line))
            if refs & aliases:
                load = re.match(r'  %[\w.]+ = load <2 x i32>, <2 x i32>\* (%[\w.]+),', line)
                if i in allowed or i in structural or (load and load[1] in pointers):
                    continue
                raise ValueError('offset table escapes or has unknown writes: '+line)
            if refs & lifetime_aliases and not (' = bitcast ' in line or '@llvm.lifetime.' in line):
                raise ValueError('offset table lifetime alias escapes')
        for pointer, (r, _) in pointers.items():
            if r == root:
                result[pointer] = [set(p[axis] for p in stores[root].values()) for axis in (0, 1)]
    return result


def vector(value, defs, tables=None, seen=()):
    if value == 'zeroinitializer':
        return [{0}, {0}]
    if value in seen:
        return [set(), set()]
    seen = (*seen, value)
    if value in ('undef', 'poison'):
        return [None, None]
    rhs = defs.get(value, '')
    if tables is not None:
        m = re.match(r'load <2 x i32>, <2 x i32>\* (%[\w.]+),', rhs)
        if m and m[1] in tables:
            return tables[m[1]]
        if rhs.startswith('phi <2 x i32> '):
            values = re.findall(r'\[ (.*?), %[^]]+ \]', rhs)
            if values:
                options = [vector(v, defs, tables, seen) for v in values]
                return [set().union(*(option[axis] for option in options)) for axis in (0, 1)]
    m = re.fullmatch('insertelement <2 x i32> (\\S+), i32 (\\S+), i(?:32|64) ([01])', defs.get(value, ''))
    if not m:
        raise ValueError('unproven vector offset ' + value)
    result = vector(m[1], defs, tables, seen)
    result[int(m[3])] = choices(m[2], defs)
    return result

def rewrite(text, include_private_tables=False):
    lines = text.splitlines()
    fs, calls = null_compare.read_functions(lines)
    count = 0
    extra = 0
    for name, rows in calls.items():
        if not re.fullmatch('air.sample_texture_2d(?:_array)?\\.v4f(?:16|32)', name):
            continue
        slot = 5 if '_array.' in name else 4
        for fn, i, args in rows:
            m = re.fullmatch('<2 x i32> (%[\\w.]+)', args[slot])
            if not m:
                continue
            f = fs[fn]
            defs = {}
            for line in lines[f['header'] + 1:f['end']]:
                d = re.match('^  (%[\\w.]+) = (.*)$', line)
                if d:
                    defs[d[1]] = d[2]
            tables = private_tables(lines[f['header'] + 1:f['end']]) if include_private_tables else None
            axes = vector(m[1], defs, tables)
            if any(not axis for axis in axes):
                raise ValueError('uninitialized texture offset component')
            pairs = list(itertools.product(*(sorted(axis) for axis in axes)))
            if len(pairs) > (9 if include_private_tables else 4):
                raise ValueError('too many sample-offset choices')
            line = lines[i]
            start, end, _ = null_compare.parameters(line, name)
            orig = line.split('=')[0].strip()
            ty = 'half' if name.endswith('f16') else 'float'
            vec = f'<4 x {ty}>'
            struct = '{ ' + vec + ', i8 }'
            out = []
            prefix = f'offset{count}.'
            if '%' + prefix in text:
                raise ValueError('offset temporary names already in use')

            def emit(n, rhs):
                value = '%' + prefix + n
                out.append('  ' + value + ' = ' + rhs)
                return value
            ox = emit('x', f'extractelement <2 x i32> {m[1]}, i32 0')
            oy = emit('y', f'extractelement <2 x i32> {m[1]}, i32 1')
            for j, (x, y) in enumerate(pairs):
                assert -8 <= x <= 7 and -8 <= y <= 7
                kept = list(args)
                kept[slot] = f'<2 x i32> <i32 {x}, i32 {y}>'
                call = line.split('=', 1)[1].strip()
                call = call[:call.index('@' + name + '(') + len(name) + 2] + ', '.join(kept) + ')' + line[end + 1:]
                r = emit(f'r{j}', call)
                p = emit(f'p{j}', f'extractvalue {struct} {r}, 0')
                s = emit(f's{j}', f'extractvalue {struct} {r}, 1')
                if j:
                    cx = emit(f'cx{j}', f'icmp eq i32 {ox}, {x}')
                    cy = emit(f'cy{j}', f'icmp eq i32 {oy}, {y}')
                    c = emit(f'c{j}', f'and i1 {cx}, {cy}')
                    p = emit(f'selp{j}', f'select i1 {c}, {vec} {p}, {vec} {pixel}')
                    s = emit(f'sels{j}', f'select i1 {c}, i8 {s}, i8 {status}')
                pixel, status = (p, s)
            r = emit('result', f'insertvalue {struct} undef, {vec} {pixel}, 0')
            out.append(f'  {orig} = insertvalue {struct} {r}, i8 {status}, 1')
            lines[i] = '\n'.join(out)
            count += 1
            extra += len(pairs) - 1
    return ('\n'.join(lines) + '\n', dict(calls=count, extra_samples=extra))
