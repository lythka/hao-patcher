#!/usr/bin/env python3
"""Widen 16-bit texture-read coordinates when the module also uses the 32-bit overload.

Example: MetalFX dbfnetv1_lowres_signals at constants 15=1/19=1. When one
module calls both `air.read_texture_2d.i16.v4f32` and `air.read_texture_2d.v4f32`,
the HD 4000 legacy request writer wraps the converted 16-bit call's result in
`insertvalue {vec, i8} undef, ..., 0` twice. The request bitcode is then invalid
("Inserted value type doesn't match aggregate type") and the old compiler aborts
in readModuleFromBinaryRequest. Calling the 32-bit overload directly with
zero-extended coordinates removes the mixed pair; the same request then compiles.

Only struct-returning `.i16.` reads whose 32-bit sibling is declared in the same
module are rewritten. Every parameter must be identical or an i16 -> i32 (scalar or
same-length vector) widening. Metal read coordinates, array indices and levels are
unsigned, so SSA operands are zero-extended; constants must be non-negative i16
literals, zeroinitializer, undef or poison. Anything else refuses.
"""
import re
import null_compare

DECLARE = re.compile(r'^declare (\{[^}]*\}) @(air\.read_[\w.]*?)\.i16\.([\w.]+)\((.*)\)(.*)$')
SIBLING = r'^declare (\{[^}]*\}) @%s\((.*)\)(.*)$'
CALL = re.compile(r'^  (%[-\w.$]+) = ((?:tail |notail |musttail )?call [^@]*)@([\w.]+)\((.*)\)((?: #\d+)?(?:, !.*)?)$')
SSA = re.compile(r'%[-\w.$]+')


def param_type(param):
    param = param.strip()
    match = re.match(r'(<\d+ x [^>]+>|%[\w.]+(?: addrspace\(\d+\))?\**|[\w]+(?: addrspace\(\d+\))?\**)', param)
    return match[1] if match else None


def widened(narrow, wide):
    if narrow == wide:
        return False
    if narrow == 'i16' and wide == 'i32':
        return True
    a = re.fullmatch(r'<(\d+) x i16>', narrow or '')
    b = re.fullmatch(r'<(\d+) x i32>', wide or '')
    if a and b and a[1] == b[1]:
        return True
    raise ValueError('texture overload parameter mismatch: %s vs %s' % (narrow, wide))


def pairs(text):
    """Return {narrow_name: (wide_name, [param types narrow], [param types wide])} for mixed modules."""
    found = {}
    for line in text.splitlines():
        match = DECLARE.match(line)
        if not match:
            continue
        narrow = match[2] + '.i16.' + match[3]
        wide = match[2] + '.' + match[3]
        sibling = re.search(SIBLING % re.escape(wide), text, re.M)
        if not sibling:
            continue
        if sibling[1] != match[1]:
            raise ValueError('texture overload return mismatch: ' + narrow)
        a = [param_type(p) for p in null_compare.split_top(match[4])]
        b = [param_type(p) for p in null_compare.split_top(sibling[2])]
        if len(a) != len(b) or None in a or None in b:
            raise ValueError('texture overload arity mismatch: ' + narrow)
        found[narrow] = (wide, a, b)
    return found


def scan(text):
    """Names of 16-bit reads still called in a module that also declares the 32-bit read."""
    if '.i16.' not in text:
        return []
    return [name for name in pairs(text) if re.search(r'@' + re.escape(name) + r'\(', text.split('\ndeclare ', 1)[0])
            or len(re.findall(r'@' + re.escape(name) + r'\b', text)) > 1]


def constant(value, wide):
    if value in ('zeroinitializer', 'undef', 'poison'):
        return value
    if re.fullmatch(r'\d+', value) and int(value) < 65536:
        return value
    vector = re.fullmatch(r'<(i16 \d+(?:, i16 \d+)*)>', value)
    if vector and len(re.findall(r'i16 ', vector[1])) == int(re.fullmatch(r'<(\d+) x i32>', wide)[1]) and all(int(n) < 65536 for n in re.findall(r'i16 (\d+)', vector[1])):
        return '<' + vector[1].replace('i16 ', 'i32 ') + '>'
    raise ValueError('unsupported texture coordinate constant: ' + value)


def rewrite(text):
    if '.i16.' not in text:
        return text, dict(calls=0, overloads=[])
    found = pairs(text)
    if not found:
        return text, dict(calls=0, overloads=[])
    lines = text.split('\n')
    out, calls, counter = [], 0, 0
    occupied = set(SSA.findall(text))
    for line in lines:
        match = CALL.match(line)
        if not match or match[3] not in found:
            for name in found:
                if re.search(r'@' + re.escape(name) + r'\b', line) and not line.startswith('declare '):
                    raise ValueError('unsupported use of texture overload: ' + name)
            out.append(line)
            continue
        if 'musttail ' in match[2]:
            raise ValueError('musttail texture call cannot change signature')
        wide, narrow_types, wide_types = found[match[3]]
        args = null_compare.split_top(match[4])
        if len(args) != len(narrow_types):
            raise ValueError('texture call arity mismatch: ' + match[3])
        new_args = []
        for arg, a, b in zip(args, narrow_types, wide_types):
            arg = arg.strip()
            if not widened(a, b):
                new_args.append(arg)
                continue
            if not arg.startswith(a + ' '):
                raise ValueError('unparsed texture argument: ' + arg)
            rest = arg[len(a) + 1:]
            attributes = []
            while re.match(r'^(noundef|signext|zeroext) ', rest):
                attr, rest = rest.split(' ', 1)
                attributes.append(attr)
            if 'signext' in attributes:
                raise ValueError('signed texture coordinate refused: ' + arg)
            attrs = ' '.join(a for a in attributes if a != 'zeroext')
            value = rest
            if SSA.fullmatch(value):
                counter += 1
                name = '%%tcw%d.%s' % (counter, match[1][1:])
                while name in occupied:
                    counter += 1
                    name = '%%tcw%d.%s' % (counter, match[1][1:])
                occupied.add(name)
                out.append('  %s = zext %s %s to %s' % (name, a, value, b))
                value = name
            else:
                value = constant(value, b)
            new_args.append(' '.join(x for x in (b, attrs, value) if x))
        out.append('  %s = %s@%s(%s)%s' % (match[1], match[2], wide, ', '.join(new_args), match[5]))
        calls += 1
    text = '\n'.join(out)
    for name in found:
        if re.search(r'@' + re.escape(name) + r'\(', text.replace('declare ', '\0', 0)) and \
                len(re.findall(r'@' + re.escape(name) + r'\b', text)) > 1:
            raise ValueError('texture overload still referenced: ' + name)
        text = re.sub(r'^declare [^\n]*@' + re.escape(name) + r'\([^\n]*\n', '', text, flags=re.M)
    if scan(text):
        raise ValueError('mixed texture overloads remain')
    return text, dict(calls=calls, overloads=sorted(found))
