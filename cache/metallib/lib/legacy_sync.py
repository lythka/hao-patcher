"""Restore the legacy AIR barrier and atomic calling conventions.

Metal 4.1 emits barriers(flags, order, scope) and atomics(..., order,
scope, flags, volatile). The Ivy Bridge runtime has barriers(flags, scope)
and atomics(..., order, scope, volatile). Its linker inserts function-pointer
bitcasts for mismatched signatures; IGIL then crashes while analysing them.

Only the old semantics are representable here: sequentially consistent
barriers, and relaxed atomics with no extra memory fences. Keep flags, scope,
values and volatility unchanged. Other orders/fences require a separate
implementation, not silently dropping an operand. Assemble with metal-as:
metal's AIR upgrade pass would put the new arguments back.
"""
import re

import null_compare
from texture_coords import param_type


NAME = r'air\.(?:(?:wg|simdgroup)\.barrier|atomic\.(?:global|local)\.(?:load|store|exchange|cmpxchg\.(?:weak|strong)|(?:add|sub|and|or|xor|min|max)(?:\.[us])?)\.(?:i32|i64|f32))'
REFERENCE = re.compile(r'@("?' + NAME + r'"?)(?=[\s,(]|$)')


def declarations(text):
    found = {}
    for index, line in enumerate(text.split('\n')):
        if not line.startswith('declare '):
            continue
        match = REFERENCE.search(null_compare.without_comment(line))
        if not match:
            continue
        printed = match[1]
        name = printed.strip('"')
        start, end, args = null_compare.parameters(line, printed)
        types = [param_type(arg) for arg in args]
        if name.endswith('.barrier'):
            if types == ['i32', 'i32']:
                continue
            if types != ['i32', 'i32', 'i32']:
                raise ValueError('unrecognized barrier signature: ' + name)
            drop, conditions = 1, {1: 'i32 5'}
        else:
            compare_exchange = '.cmpxchg.' in name
            arity = 8 if compare_exchange else 5 if '.load.' in name else 6
            if len(args) == arity - 1:
                continue
            if len(args) != arity or types[-4:] != ['i32', 'i32', 'i32', 'i1']:
                raise ValueError('unrecognized atomic signature: ' + name)
            drop, conditions = arity - 2, {arity - 4: 'i32 0', arity - 2: 'i32 0'}
            if compare_exchange:
                conditions[arity - 5] = 'i32 0'  # success and failure orders must both be relaxed
        found[printed] = dict(index=index, start=start, end=end, args=args,
                              drop=drop, conditions=conditions)
    return found


def scan(text):
    return sorted(declarations(text))


def rewrite(text):
    forms = declarations(text)
    if not forms:
        return text, dict(calls=0, overloads=[])
    lines = text.split('\n')
    _, calls = null_compare.read_functions(lines)
    replacements, handled, count = {}, set(), 0
    for name, form in forms.items():
        index = form['index']
        args = [a for i, a in enumerate(form['args']) if i != form['drop']]
        replacements[index] = lines[index][:form['start']] + ', '.join(args) + lines[index][form['end']:]
        handled.add(index)
        for _, index, args in calls.get(name, []):
            line = lines[index]
            if re.search(r'\bmusttail\b', line[:line.index('@' + name)]):
                raise ValueError('musttail synchronization call: ' + name)
            if len(args) != len(form['args']):
                raise ValueError('synchronization call arity differs: ' + name)
            for operand, expected in form['conditions'].items():
                if args[operand] != expected:
                    raise ValueError('unrepresentable legacy synchronization operand: ' + args[operand])
            start, end, _ = null_compare.parameters(line, name)
            kept = [a for i, a in enumerate(args) if i != form['drop']]
            replacements[index] = line[:start] + ', '.join(kept) + line[end:]
            handled.add(index)
            count += 1
    for index, line in enumerate(lines):
        if index not in handled and not line.startswith(('!', ';')):
            if any(m[1] in forms for m in REFERENCE.finditer(null_compare.without_comment(line))):
                raise ValueError('unsupported synchronization reference: ' + line)
    for index, line in replacements.items():
        lines[index] = line
    return '\n'.join(lines), dict(calls=count, overloads=sorted(forms))


def refuse_bad_state(text):
    if scan(text):
        raise ValueError('new synchronization signatures remain')
