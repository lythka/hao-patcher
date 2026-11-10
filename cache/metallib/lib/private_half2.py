"""Bit-exact fallback for mutable, dynamically indexed private half2 arrays.

Each half's 16 bits occupy one zero-extended i32 in private storage. Volatile
integer reads prevent the old backend from combining truncations into scratch
half loads. The storage is new, private and cannot escape. Volatility adds no
observable device-memory access. Every load/store and lifetime size must be
accounted for before a root is transformed. No floating-point conversion occurs.
"""
import re
import math
import null_compare

ARRAY = r'(?:\[\d+ x )+<2 x half>\]+'
ROOT = re.compile(r'^  (%[-\w.$]+) = alloca (' + ARRAY + r'), align (\d+)$')
LOAD = re.compile(r'^  (%[-\w.$]+) = load(?: volatile)? <2 x half>, <2 x half>\* (%[-\w.$]+), align (\d+)(?:, !.*)?$')
STORE = re.compile(r'^  store(?: volatile)? <2 x half> (.*), <2 x half>\* (%[-\w.$]+), align (\d+)(?:, !.*)?$')


def rewrite_function(lines):
    occupied = set(re.findall(r'%[-\w.$]+', '\n'.join(lines)))
    def reserve(base):
        name, k = base, 0
        while name in occupied:
            k += 1
            name = base + '.' + str(k)
        occupied.add(name)
        return name
    count = 0
    for rootloc, rootline in enumerate(list(lines)):
        root = ROOT.match(rootline)
        if not root:
            continue
        pointers = {root[1]}
        geps, casts = {}, {}
        changed = True
        while changed:
            changed = False
            for i, line in enumerate(lines):
                m = null_compare.SSA.match(line)
                if not m or m[1] in pointers:
                    continue
                if m[2].startswith('getelementptr '):
                    parts = null_compare.split_top(m[2].split(' ', 1)[1].removeprefix('inbounds '))
                    if len(parts) > 1:
                        try:
                            parent = null_compare.last_value(parts[1])
                        except ValueError:
                            continue
                        if parent in pointers and '<2 x half>' in parts[0]:
                            pointers.add(m[1]); geps[i] = line; changed = True
        for i, line in enumerate(lines):
            m = re.match(r'^  (%[-\w.$]+) = bitcast (.*) (%[-\w.$]+) to i8\*$', line)
            if m and m[3] == root[1] and m[2] == root[2] + '*':
                casts[m[1]] = i
        edits, good, nload = {}, True, 0
        root_bytes = 4 * math.prod(int(n) for n in re.findall(r'\[(\d+) x ', root[2]))
        for i, line in enumerate(lines):
            used = set(re.findall(r'%[-\w.$]+', line))
            definition = null_compare.SSA.match(line)
            if definition:
                used.discard(definition[1])
            if used & pointers:
                if i in geps or i in casts.values():
                    edits[i] = line.replace('<2 x half>', '<2 x i32>')
                    continue
                m = LOAD.match(line)
                if m and m[2] in pointers:
                    bits = reserve('%compat.wide.' + m[1][1:] + '.bits')
                    half = reserve('%compat.wide.' + m[1][1:] + '.half')
                    edits[i] = (f'  {bits} = load volatile <2 x i32>, <2 x i32>* {m[2]}, align {m[3]}\n'
                                f'  {half} = trunc <2 x i32> {bits} to <2 x i16>\n'
                                f'  {m[1]} = bitcast <2 x i16> {half} to <2 x half>')
                    nload += 1
                    continue
                m = STORE.match(line)
                if m and m[2] in pointers:
                    bits = reserve('%compat.wide.store.' + str(i) + '.bits')
                    wide = reserve('%compat.wide.store.' + str(i) + '.wide')
                    vol = ' volatile' if 'store volatile ' in line else ''
                    edits[i] = (f'  {bits} = bitcast <2 x half> {m[1]} to <2 x i16>\n'
                                f'  {wide} = zext <2 x i16> {bits} to <2 x i32>\n'
                                f'  store{vol} <2 x i32> {wide}, <2 x i32>* {m[2]}, align {m[3]}')
                    continue
                good = False
            if used & set(casts):
                m = re.fullmatch(r'(  call void @llvm\.lifetime\.(?:start|end)\.p0i8\(i64 )(-?\d+)(, i8\* (?:nonnull )?(%[-\w.$]+)\).*)', line)
                if m and m[4] in casts:
                    size = int(m[2]); size = size * 2 if size >= 0 else size
                    edits[i] = m[1] + str(size) + m[3]
                    continue
                zero = re.fullmatch(r'(  call void @llvm\.memset\.p0i8\.i64\(i8\* (?:(?:noundef|nonnull|align \d+|dereferenceable\(\d+\)) )*)(%[-\w.$]+)(, i8 0, i64 )(\d+)(, i1 false\).*)', line)
                if zero and zero[2] in casts and int(zero[4]) == root_bytes:
                    # Only exact whole-root zeroing has the same widened bit
                    # representation. Never resize copies or arbitrary byte fills.
                    attributes = zero[1]
                    extents = re.findall(r'dereferenceable\((\d+)\)', attributes)
                    aligns = re.findall(r'align (\d+)', attributes)
                    if any(int(n) != root_bytes for n in extents) or any(int(n) > int(root[3]) for n in aligns):
                        good = False
                        continue
                    attributes = attributes.replace(f'dereferenceable({root_bytes})', f'dereferenceable({root_bytes * 2})')
                    edits[i] = attributes + zero[2] + zero[3] + str(root_bytes * 2) + zero[5]
                else:
                    good = False
        if good and nload:
            edits[rootloc] = rootline.replace('<2 x half>', '<2 x i32>')
            # Keep line indices stable while accounting for multiple arrays.
            lines = [edits.get(i, line) for i, line in enumerate(lines)]
            count += 1
    return lines, count


def flatten_matrices(lines, types):
    """Flatten only closed local storage of an unpacked one-array-field type.

    Public/other uses of the named type stay untouched. Adopt a candidate only
    after the ordinary array transform proves and widens that exact root.
    """
    arrays = matrices = 0
    for rootloc, line in enumerate(list(lines)):
        root = re.fullmatch(r'  (%[-\w.$]+) = alloca (%"[^"]+"|%[-\w.$]+), align (\d+)', line)
        if not root or root[2] not in types:
            continue
        array = types[root[2]]
        edits = {rootloc: f'  {root[1]} = alloca {array}, align {root[3]}'}
        good = True
        for i, instruction in enumerate(lines):
            if i == rootloc:
                continue
            used = set(re.findall(r'%[-\w.$]+', instruction))
            definition = null_compare.SSA.match(instruction)
            if definition:
                used.discard(definition[1])
            if root[1] not in used:
                continue
            if definition and definition[2].startswith('getelementptr '):
                operation = definition[2].split(' ', 1)[1]
                inbounds = operation.startswith('inbounds ')
                parts = null_compare.split_top(operation.removeprefix('inbounds '))
                if (len(parts) >= 4 and parts[0] == root[2]
                        and parts[1] == root[2] + '* ' + root[1]
                        and re.fullmatch(r'i(?:32|64) 0', parts[2])
                        and parts[3] == 'i32 0'):
                    operands = [array, array + '* ' + root[1], parts[2], *parts[4:]]
                    edits[i] = '  ' + definition[1] + ' = getelementptr ' + ('inbounds ' if inbounds else '') + ', '.join(operands)
                    continue
            if re.fullmatch(r'  %[-\w.$]+ = bitcast ' + re.escape(root[2] + '* ' + root[1]) + r' to i8\*', instruction):
                edits[i] = instruction.replace(root[2] + '* ' + root[1], array + '* ' + root[1])
                continue
            good = False
        if good:
            candidate = [edits.get(i, instruction) for i, instruction in enumerate(lines)]
            widened, count = rewrite_function(candidate)
            if '<2 x i32>' in widened[rootloc]:
                lines = widened
                arrays += count
                matrices += 1
    return lines, arrays, matrices


def rewrite(text):
    lines = text.split('\n')
    functions, _ = null_compare.read_functions(lines)
    touched, arrays, matrices = [], 0, 0
    types = {}
    for line in lines:
        match = re.fullmatch(r'(%"[^"]+"|%[-\w.$]+) = type \{ (' + ARRAY + r') \}', line)
        if match:
            types[match[1]] = match[2]
    for name, fn in reversed(list(functions.items())):
        start, end = fn['header'], fn['end'] + 1
        body, matrix_arrays, matrix_count = flatten_matrices(lines[start:end], types)
        body, count = rewrite_function(body)
        count += matrix_arrays
        matrices += matrix_count
        if count:
            lines[start:end] = body
            arrays += count
            touched.append(name)
    result = '\n'.join(lines)
    if touched:
        from memory_effects import relax_private_volatile
        result = relax_private_volatile(result, touched)
    return result, dict(arrays=arrays, matrices=matrices, functions=sorted(touched))
