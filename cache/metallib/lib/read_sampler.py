"""Convert AIR 2.8 implicit-sampler, zero-offset 2D reads to direct reads.

The retained request downgrader crashes on the newer signature. Paired VFX
captures establish the direct-read conversion before that writer. Only the
builtin read sampler and a zero offset are removed. User samplers and nonzero
offsets cannot be discarded. Other arguments, result types and metadata remain.
"""
import re
import null_compare
import texture_coords

NAME = r'air\.read_(?:texture|depth)_2d\.[\w.]+'
DECL = re.compile(r'^declare .*?@("?' + NAME + r'"?)\(')
REFERENCE = re.compile(r'@("?' + NAME + r'"?)(?=[\s,(])')
GETTER = 'air.get_read_sampler'


def references(line):
    # Quoted IR identifiers follow @ or %. Ordinary quoted strings are data.
    line = null_compare.without_comment(line)
    line = re.sub(r'"(?:[^"\\]|\\.)*"',
                  lambda m: m[0] if m.start() and line[m.start() - 1] in '@%' else ' ' * len(m[0]), line)
    return REFERENCE.finditer(line)


def declarations(lines):
    found = {}
    for index, raw in enumerate(lines):
        line = null_compare.without_comment(raw)
        match = DECL.match(line)
        if not match:
            continue
        printed = match[1]
        name = printed.strip('"')
        start, end, args = null_compare.parameters(line, printed)
        if len(args) < 2 or '_sampler_t' not in args[1]:
            continue
        depth = name.startswith('air.read_depth_')
        width = 'i16' if '.i16.' in name else 'i32'
        offset = 4 if depth else 3
        sampler = re.fullmatch(r'%(?:struct\._sampler_t|"struct\._sampler_t") addrspace\(2\)\*(?: .*)?', args[1])
        types = [texture_coords.param_type(arg) for arg in args]
        expected = [types[0], types[1]] + (['i32'] if depth else []) + [
            '<2 x ' + width + '>', '<2 x ' + width + '>', width, 'i32']
        if not sampler or types != expected:
            raise ValueError('unrecognized sampler-form read signature: ' + name)
        found[name] = dict(index=index, start=start, end=end, args=args,
                           offset=offset, printed=printed)
    return found


def scan(text):
    if '_sampler_t' not in text or 'air.read_' not in text:
        return []
    lines = text.split('\n')
    forms = declarations(lines)
    used = set()
    for raw in lines:
        line = null_compare.without_comment(raw)
        if line.startswith('declare '):
            continue
        used.update(m[1].strip('"') for m in references(line)
                    if m[1].strip('"') in forms)
    return sorted(used)


def rewrite(text):
    names = set(scan(text))
    if not names:
        return text, dict(calls=0, overloads=[])
    lines = text.split('\n')
    forms = {name: item for name, item in declarations(lines).items() if name in names}
    _, calls = null_compare.read_functions(lines)
    samplers = {}
    for printed, sites in calls.items():
        if printed.strip('"') != GETTER:
            continue
        for fn, index, args in sites:
            match = null_compare.SSA.match(null_compare.without_comment(lines[index]))
            if match and not args:
                samplers.setdefault(fn, set()).add(match[1])
    replacements, handled, count = {}, set(), 0
    for name, form in forms.items():
        args = [arg for i, arg in enumerate(form['args']) if i not in (1, form['offset'])]
        line = lines[form['index']]
        replacements[form['index']] = line[:form['start']] + ', '.join(args) + line[form['end']:]
        handled.add(form['index'])
    for printed, sites in calls.items():
        name = printed.strip('"')
        if name not in forms:
            continue
        form = forms[name]
        for fn, index, args in sites:
            line = null_compare.without_comment(lines[index])
            if re.search(r'\bmusttail\b', line[:line.index('@' + printed)]):
                raise ValueError('musttail read cannot change its signature')
            if len(args) != len(form['args']):
                raise ValueError('sampler-form read call arity differs: ' + name)
            if null_compare.last_value(args[1]) not in samplers.get(fn, set()):
                raise ValueError('read sampler is not the local builtin result: ' + name)
            offset = form['offset']
            offset_type = texture_coords.param_type(form['args'][offset])
            width = 'i16' if '.i16.' in name else 'i32'
            zeros = (offset_type + ' zeroinitializer',
                     offset_type + ' <' + width + ' 0, ' + width + ' 0>')
            if args[offset] not in zeros:
                raise ValueError('read coordinate offset is not zero: ' + args[offset])
            start, end, _ = null_compare.parameters(line, printed)
            kept = [arg for i, arg in enumerate(args) if i not in (1, offset)]
            replacements[index] = lines[index][:start] + ', '.join(kept) + lines[index][end:]
            handled.add(index)
            count += 1
    for index, raw in enumerate(lines):
        if index not in handled and any(m[1].strip('"') in forms
                                        for m in references(raw)):
            raise ValueError('unrecognized sampler-form read use: ' + raw)
    for index, line in replacements.items():
        lines[index] = line
    out = '\n'.join(lines)
    if scan(out):
        raise ValueError('sampler-form reads remain after conversion')
    return out, dict(calls=count, overloads=sorted(forms))
