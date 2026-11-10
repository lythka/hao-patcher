#!/usr/bin/env python3
"""Lower dynamically indexed, constant private half2 tables to SSA selections.

The old Intel selector has no private half2 load. Splitting the load into
halves still needs unsupported scratch half loads; loading i32 folds back to
the failing vector load. Constant tables can instead be read with integer
index comparisons and exact half-vector constants, without memory accesses.
Only entry-block, complete constant initialization with no escaping pointer
or later writes is accepted. Other private half2 loads remain refusing states.
"""
import re
import null_compare

LOAD = re.compile(r'^  (%[-\w.$]+) = load(?: volatile)? <2 x half>, <2 x half>\* (%[-\w.$]+), align (\d+)(.*)$')
ALLOCA = re.compile(r'^  (%[-\w.$]+) = alloca \[(\d+) x <2 x half>\], align \d+$')
GEP = re.compile(r'^  (%[-\w.$]+) = getelementptr inbounds \[(\d+) x <2 x half>\], \[\2 x <2 x half>\]\* (%[-\w.$]+), i\d+ 0, (i\d+) (%[-\w.$]+|-?\d+)$')
STORE = re.compile(r'^  store <2 x half> (zeroinitializer|<half (?:0xH[0-9A-Fa-f]+|[-+\d.eE]+), half (?:0xH[0-9A-Fa-f]+|[-+\d.eE]+)>), <2 x half>\* (%[-\w.$]+), align \d+(?:, !.*)?$')


def scan(text, load_pattern=LOAD):
    if not any(load_pattern.match(line) for line in text.splitlines()):
        return []
    lines = text.split('\n')
    functions, calls = null_compare.read_functions(lines)
    dynamic = set()
    changed = True
    while changed:
        changed = False
        for name, function in functions.items():
            for value, (_, expression) in function['values'].items():
                if (name, value) in dynamic:
                    continue
                hit = False
                if expression.startswith('getelementptr '):
                    operands = null_compare.split_top(expression.split(' ', 1)[1].removeprefix('inbounds '))
                    hit = any(re.fullmatch(r'i\d+ %[-\w.$]+', operand) for operand in operands[2:])
                    if len(operands) > 1:
                        hit |= (name, null_compare.last_value(operands[1])) in dynamic
                elif expression.startswith(('bitcast ', 'phi ', 'select ')):
                    hit = any((name, operand) in dynamic for operand in re.findall(r'%[-\w.$]+', expression))
                if hit:
                    dynamic.add((name, value))
                    changed = True
        for callee, sites in calls.items():
            if callee not in functions or not functions[callee]['internal']:
                continue
            args = {i: value for value, i in functions[callee]['arg_values'].items()}
            for caller, _, actual in sites:
                for index, value in args.items():
                    if index < len(actual) and (callee, value) not in dynamic:
                        try:
                            hit = (caller, null_compare.last_value(actual[index])) in dynamic
                        except ValueError:
                            hit = False
                        if hit:
                            dynamic.add((callee, value))
                            changed = True
    result = []
    fn = None
    for line in text.splitlines():
        m = null_compare.DEFINITION.match(line)
        if m:
            fn = m[1]
        m = load_pattern.match(line)
        if m and (fn, m[2]) in dynamic:
            result.append(dict(function=fn, result=m[1], pointer=m[2]))
    return result


def rewrite_function(lines):
    allocas, geps, stores = {}, {}, {}
    entry = True
    loads = []
    for i, line in enumerate(lines):
        if re.match(r'^  (?:br|switch|indirectbr|invoke)\b', line):
            entry = False
        m = ALLOCA.match(line)
        if m and entry:
            allocas[m[1]] = int(m[2])
        m = GEP.match(line)
        if m:
            geps[m[1]] = (m[3], int(m[2]), m[4], m[5], i)
        m = STORE.match(line)
        if m:
            stores.setdefault(m[2], []).append((m[1], i, entry))
        m = LOAD.match(line)
        if m:
            loads.append((i, m))
    changed, tables = {}, set()
    occupied = set(re.findall(r'%[-\w.$]+', '\n'.join(lines)))
    def reserve(base):
        name, k = base, 0
        while name in occupied:
            k += 1
            name = base + '.' + str(k)
        occupied.add(name)
        return name
    for location, m in loads:
        if 'load volatile ' in lines[location]:
            continue
        if m[2] not in geps:
            continue
        root, count, idx_ty, idx, _ = geps[m[2]]
        if allocas.get(root) != count or not 1 <= count <= 1024:
            continue
        family = {p: g for p, g in geps.items() if g[0] == root}
        constants = {}
        good = True
        for p, g in family.items():
            writes = stores.get(p, [])
            if writes:
                if len(writes) != 1 or not writes[0][2] or writes[0][1] >= location or not g[3].isdigit():
                    good = False
                    break
                k = int(g[3])
                if k in constants or not 0 <= k < count:
                    good = False
                    break
                constants[k] = writes[0][0]
        if not good or set(constants) != set(range(count)):
            continue
        # Enumerate every pointer use. Allow only the known accesses, GEPs,
        # and lifetime-only bitcasts. This rejects aliases and hidden writes.
        ptrs = {root, *family}
        casts = {}
        for j, line in enumerate(lines):
            c = re.match(r'^  (%[-\w.$]+) = bitcast .*\* (%[-\w.$]+) to i8\*$', line)
            if c and c[2] == root:
                casts[c[1]] = j
        for j, line in enumerate(lines):
            used = set(re.findall(r'%[-\w.$]+', line))
            definition = re.match(r'^  (%[-\w.$]+) = ', line)
            if definition:
                used.discard(definition[1])
            if used & ptrs:
                gep = GEP.match(line)
                if not ((gep and gep[1] in family) or STORE.match(line) or LOAD.match(line) or j in casts.values()):
                    good = False
            if used & set(casts) and not re.search(r'call void @llvm\.lifetime\.(?:start|end)\.p0i8\(', line):
                good = False
        if not good:
            continue
        out = []
        value = constants[count - 1]
        for k in reversed(range(count - 1)):
            test = reserve('%compat.half2.' + m[1][1:] + '.is' + str(k))
            result = m[1] if k == 0 else reserve('%compat.half2.' + m[1][1:] + '.v' + str(k))
            out.append(f'  {test} = icmp eq {idx_ty} {idx}, {k}')
            out.append(f'  {result} = select i1 {test}, <2 x half> {constants[k]}, <2 x half> {value}')
            value = result
        if count == 1:
            out.append(f'  {m[1]} = select i1 true, <2 x half> {value}, <2 x half> {value}')
        changed[location] = '\n'.join(out)
        tables.add(root)
    return [changed.get(i, line) for i, line in enumerate(lines)], dict(loads=len(changed), tables=len(tables))


def rewrite(text):
    lines = text.split('\n')
    functions, _ = null_compare.read_functions(lines)
    loads, tables, touched = 0, 0, []
    for name, fn in reversed(list(functions.items())):
        a, b = fn['header'], fn['end'] + 1
        body, report = rewrite_function(lines[a:b])
        if report['loads']:
            lines[a:b] = body
            loads += report['loads']
            tables += report['tables']
            touched.append(name)
    return '\n'.join(lines), dict(loads=loads, tables=tables, functions=sorted(touched))
