#!/usr/bin/env python3
"""Thread explicit device-pointer validity through local SSA and internal calls.

No function or SSA names select a rewrite. Unknown pointer origins refuse;
public parameter validity requires a nonnull contract or an entry-block
load through that parameter. No nullable parameter is assumed valid merely
because it is a Metal buffer. Nullable public values use a private scalar spill, never a pointer-to-integer cast.
"""
import argparse
from collections import defaultdict, deque
import json
import re
from pathlib import Path

VALUE = r'(?:%[-\w.$]+|@[\w.$-]+|null)'
CMP = re.compile(r'^  (%[-\w.$]+) = icmp (eq|ne) (.+?addrspace\(1\)\*) (' + VALUE + r'), (' + VALUE + r')(.*)$')
DEFINITION = re.compile(r'^define .*?@("[^"]+"|[^ (]+)\(')
CALL = re.compile(r'\bcall\b[^@\n]*@("[^"]+"|[^ (]+)\(')
SSA = re.compile(r'^  (%[-\w.$]+) = (.*)$')


class UnsupportedValidity(ValueError):
    pass


def split_top(text):
    out, start, depth, quoted = [], 0, 0, False
    for i, char in enumerate(text):
        if char == '"' and (i == 0 or text[i - 1] != '\\'):
            quoted = not quoted
        if quoted:
            continue
        if char in '([{<':
            depth += 1
        elif char in ')]}>':
            depth -= 1
        elif char == ',' and depth == 0:
            out.append(text[start:i].strip())
            start = i + 1
    out.append(text[start:].strip())
    return out if text.strip() else []


def parameters(line, name):
    start = line.index('@' + name + '(') + len(name) + 2
    depth, quoted = 1, False
    for end in range(start, len(line)):
        char = line[end]
        if char == '"' and line[end - 1] != '\\':
            quoted = not quoted
        if not quoted:
            depth += (char == '(') - (char == ')')
            if depth == 0:
                return start, end, split_top(line[start:end])
    raise ValueError('unterminated argument list')


def last_value(text):
    match = re.search(r'(' + VALUE + r')\s*$', text)
    if not match:
        raise UnsupportedValidity('unsupported pointer expression: ' + text)
    return match[1]


def without_comment(line):
    return re.match(r'(?:"(?:[^"\\]|\\.)*"|[^";])*', line)[0]


def parameter_nonnull(argument):
    # LLVM prints attributes as separate tokens. A type/SSA identifier such as
    # %nonnull and a quoted string containing the word are never attributes.
    tokens = re.findall(r'"(?:[^"\\]|\\.)*"|[^"\s]+', argument)
    for token in tokens:
        if token == 'nonnull':
            return True
        match = re.fullmatch(r'dereferenceable\((\d+)\)', token)
        if match and int(match[1]) > 0:
            return True
    return False


def read_functions(lines):
    functions, calls = {}, defaultdict(list)
    current = None
    for i, line in enumerate(lines):
        line = without_comment(line)
        match = DEFINITION.match(line)
        if match:
            current = match[1]
            _, _, args = parameters(line, current)
            functions[current] = dict(name=current, header=i, end=None, args=args,
                                      internal=bool(re.match(r'^define (?:internal|private)\b', line)),
                                      values={}, arg_values={}, entry_dereferences=set())
            for index, arg in enumerate(args):
                value = re.search(r'(%[-\w.$]+)$', arg)
                if value:
                    functions[current]['arg_values'][value[1]] = index
            in_entry = True
            continue
        if current is None:
            continue
        function = functions[current]
        if line == '}':
            function['end'] = i
            current = None
            continue
        if re.match(r'^[\w.$-]+:', line) or re.match(r'^  (?:br|switch|indirectbr|invoke)\b', line):
            in_entry = False
        instruction = SSA.match(line)
        if in_entry and instruction and instruction[2].startswith('load '):
            pieces = split_top(instruction[2][len('load '):])
            if len(pieces) >= 2:
                try:
                    function['entry_dereferences'].add(last_value(pieces[1]))
                except UnsupportedValidity:
                    pass
        value = SSA.match(line)
        if value:
            function['values'][value[1]] = (i, value[2])
        match = CALL.search(line)
        if match:
            _, _, args = parameters(line, match[1])
            calls[match[1]].append((current, i, args))
    return functions, calls


def scan(text):
    result, function = [], None
    for line in text.splitlines():
        match = DEFINITION.match(line)
        if match:
            function = match[1]
        match = CMP.match(line)
        if match and ('null' in (match[4], match[5])):
            result.append(dict(function=function, result=match[1], predicate=match[2],
                               type=match[3], pointer=match[5] if match[4] == 'null' else match[4]))
    return result


def rewrite(text):
    matches = scan(text)
    if not matches:
        return text, dict(compares=0, phis=0, selects=0, threaded_parameters=0, calls=0)
    lines = text.split('\n')
    functions, calls = read_functions(lines)
    expressions, needed = {}, defaultdict(set)
    pending = deque()
    pointer_sizes = {0: 64}
    layout = next((line for line in lines if line.startswith('target datalayout =')), '')
    for space, width in re.findall(r'(?:^|[-\"])p(\d*):(\d+):', layout):
        pointer_sizes[int(space or 0)] = int(width)

    def validity(fn, value):
        key = (fn, value)
        if value == 'null':
            return ('constant', False)
        if key in expressions:
            return key
        function = functions[fn]
        if value.startswith('@'):
            decl = next((line for line in lines if line.startswith(value + ' =')), '')
            if not decl or 'extern_weak' in decl:
                raise UnsupportedValidity((fn, value, 'unproved global validity'))
            expressions[key] = ('constant', True)
        elif value in function['arg_values']:
            index = function['arg_values'][value]
            if function['internal']:
                expressions[key] = ('argument', index)
                if index not in needed[fn]:
                    needed[fn].add(index)
                    pending.append((fn, index))
            elif (parameter_nonnull(function['args'][index])
                  or value in function['entry_dereferences']):
                expressions[key] = ('constant', True)
            else:
                expressions[key] = ('spill', None, function['args'][index][:function['args'][index].index('addrspace(1)*') + len('addrspace(1)*')])
        else:
            if value not in function['values']:
                raise UnsupportedValidity((fn, value, 'unknown SSA origin'))
            location, expression = function['values'][value]
            # Reserve before recursion so loop-carried phis can refer to their flag.
            expressions[key] = ('pending',)
            match = re.match(r'phi (.+?addrspace\(1\)\*) (.*)$', expression)
            if match:
                incoming = []
                for pair in split_top(match[2]):
                    pair_match = re.fullmatch(r'\[\s*(' + VALUE + r'),\s*(%[-\w.$]+)\s*\]', pair)
                    if not pair_match:
                        raise UnsupportedValidity((fn, value, 'unsupported phi operand'))
                    incoming.append((validity(fn, pair_match[1]), pair_match[2]))
                expressions[key] = ('phi', location, incoming)
            elif expression.startswith('select '):
                arms = split_top(expression[7:])
                if len(arms) != 3 or not arms[0].startswith('i1 '):
                    raise UnsupportedValidity((fn, value, 'unsupported pointer select'))
                expressions[key] = ('select', location, arms[0][3:],
                                    validity(fn, last_value(arms[1])), validity(fn, last_value(arms[2])))
            elif expression.startswith('bitcast '):
                source = expression[8:].split(' to ', 1)[0]
                expressions[key] = ('alias', validity(fn, last_value(source)))
            elif expression.startswith('load '):
                operands = split_top(expression[5:])
                if len(operands) < 2 or not operands[0].endswith('addrspace(1)*'):
                    raise UnsupportedValidity((fn, value, 'unsupported pointer load'))
                if any(re.match(r'!(?:nonnull|dereferenceable)\b', part) for part in operands[2:]):
                    expressions[key] = ('constant', True)
                else:
                    pointer = operands[1]
                    if not pointer.startswith(operands[0]):
                        raise UnsupportedValidity((fn, value, 'unsupported pointer storage type'))
                    suffix = pointer[len(operands[0]):]
                    storage = re.fullmatch(r'( addrspace\(\d+\))?\* (%[-\w.$]+|@[\w.$-]+)', suffix)
                    allowed_md = r'!(?:tbaa|tbaa\.struct|alias\.scope|noalias|nontemporal|invariant\.load|invariant\.group|dbg)\b'
                    if not storage or any(not (re.fullmatch(r'align \d+', part) or re.match(allowed_md, part)) for part in operands[2:]):
                        raise UnsupportedValidity((fn, value, 'unsupported pointer load metadata or storage'))
                    width = pointer_sizes.get(1, pointer_sizes[0])
                    expressions[key] = ('loaded_integer', location, 'i' + str(width), operands, storage[1] or '')
            elif expression.startswith('inttoptr '):
                source = re.fullmatch(r'inttoptr (i\d+) (%[-\w.$]+|\d+) to .+addrspace\(1\)\*', expression)
                if not source:
                    raise UnsupportedValidity((fn, value, 'unsupported integer pointer source'))
                if int(source[1][1:]) != pointer_sizes.get(1, pointer_sizes[0]):
                    raise UnsupportedValidity((fn, value, 'integer width differs from pointer width'))
                expressions[key] = ('integer', location, source[1], source[2])
            elif re.search(r'\bcall\b.*addrspace\(1\)\*\s+@', expression):
                ty = re.search(r'\bcall\s+(.+?addrspace\(1\)\*)\s+@', expression)
                if not ty or not re.fullmatch(r'[\w.]+ addrspace\(1\)\*', ty[1]):
                    raise UnsupportedValidity((fn, value, 'unsupported pointer return type'))
                expressions[key] = ('spill', location, ty[1])
            elif expression.startswith('getelementptr inbounds '):
                operands = split_top(expression[len('getelementptr inbounds '):])
                expressions[key] = ('alias', validity(fn, last_value(operands[1])))
            else:
                raise UnsupportedValidity((fn, value, 'unsupported pointer origin: ' + expression[:180]))
        return key

    for match in matches:
        validity(match['function'], match['pointer'])
    call_flags = {}
    while pending:
        callee, index = pending.popleft()
        if not calls[callee]:
            raise UnsupportedValidity((callee, index, 'no direct caller'))
        for caller, location, args in calls[callee]:
            if index >= len(args):
                raise UnsupportedValidity('call arity mismatch')
            call_flags[(location, index)] = validity(caller, last_value(args[index]))
    for fn in needed:
        allowed = {functions[fn]['header']} | {site[1] for site in calls[fn]}
        token = re.compile(r'@' + re.escape(fn) + r'(?=[ (,)]|$)')
        if any(token.search(line) and i not in allowed and not line.startswith((';', '!'))
               for i, line in enumerate(lines)):
            raise UnsupportedValidity('indirect reference to threaded function: ' + fn)
    names, occupied = {}, {}
    for fn, function in functions.items():
        occupied[fn] = set(re.findall(r'%[-\w.$]+', '\n'.join(lines[function['header']:function['end'] + 1])))

    def reserve(fn, base):
        candidate, suffix = base, 0
        while candidate in occupied[fn]:
            suffix += 1
            candidate = base + '.' + str(suffix)
        occupied[fn].add(candidate)
        return candidate

    for key, expression in expressions.items():
        fn, value = key
        if expression[0] == 'argument':
            names[key] = reserve(fn, '%valid' if len(needed[fn]) == 1 else '%valid.' + value[1:])
        elif expression[0] in ('phi', 'select', 'integer', 'loaded_integer', 'spill'):
            names[key] = reserve(fn, '%valid' + value[1:])

    def flag(key):
        if key[0] == 'constant':
            return 'true' if key[1] else 'false'
        expression = expressions[key]
        if expression[0] == 'constant':
            return 'true' if expression[1] else 'false'
        if expression[0] == 'alias':
            return flag(expression[1])
        if key not in names:
            raise UnsupportedValidity('unresolved validity cycle')
        return names[key]

    after = defaultdict(list)
    load_changes = {}
    before = defaultdict(list)
    spilling = set()
    for key, expression in expressions.items():
        if expression[0] == 'phi':
            incoming = ', '.join('[ ' + flag(k) + ', ' + label + ' ]' for k, label in expression[2])
            after[expression[1]].append('  ' + names[key] + ' = phi i1 ' + incoming)
        elif expression[0] == 'select':
            after[expression[1]].append('  ' + names[key] + ' = select i1 ' + expression[2] + ', i1 ' + flag(expression[3]) + ', i1 ' + flag(expression[4]))
        elif expression[0] == 'integer':
            after[expression[1]].append('  ' + names[key] + ' = icmp ne ' + expression[2] + ' ' + expression[3] + ', 0')
        elif expression[0] == 'spill':
            fn, value = key
            _, location, ty = expression
            if pointer_sizes.get(1, pointer_sizes[0]) != 64 or pointer_sizes[0] != 64:
                raise UnsupportedValidity('private scalar spill requires 64-bit pointers')
            # Strip parameter attributes; only the pointer type belongs in alloca.
            if not re.fullmatch(r'(?:[\w.]+|%[-\w.$]+|%"(?:[^"\\]|\\.)*"|<\d+ x [\w.]+>)(?:\*| addrspace\(\d+\)\*)* addrspace\(1\)\*', ty):
                raise UnsupportedValidity((fn, value, 'unsupported spill pointer type: ' + ty))
            slot = reserve(fn, '%valid.slot.' + value[1:])
            address = reserve(fn, '%valid.address.' + value[1:])
            bits = reserve(fn, '%valid.bits.' + value[1:])
            instructions = [f'  {slot} = alloca {ty}, align 8',
                            f'  store volatile {ty} {value}, {ty}* {slot}, align 8',
                            f'  {address} = bitcast {ty}* {slot} to i64*',
                            f'  {bits} = load volatile i64, i64* {address}, align 8',
                            f'  {names[key]} = icmp ne i64 {bits}, 0']
            if location is None:
                first = functions[fn]['header'] + 1
                while first < functions[fn]['end'] and not lines[first].startswith('  '):
                    first += 1
                before[first] += instructions
            else:
                after[location] += instructions
            spilling.add(fn)
        elif expression[0] == 'loaded_integer':
            fn, value = key
            _, location, integer_type, operands, storage_space = expression
            address = reserve(fn, '%valid.address.' + value[1:])
            bits = reserve(fn, '%valid.bits.' + value[1:])
            int_pointer_type = integer_type + storage_space + '*'
            load_changes[location] = ('  ' + address + ' = bitcast ' + operands[1] + ' to ' + int_pointer_type + '\n'
                                     + '  ' + bits + ' = load ' + integer_type + ', ' + int_pointer_type + ' ' + address
                                     + (', ' + ', '.join(operands[2:]) if len(operands) > 2 else '') + '\n'
                                     + '  ' + value + ' = inttoptr ' + integer_type + ' ' + bits + ' to ' + operands[0])
            after[location].append('  ' + names[key] + ' = icmp ne ' + integer_type + ' ' + bits + ', 0')
    changed, call_count = dict(load_changes), 0
    for fn, indexes in needed.items():
        if not indexes:
            continue
        function = functions[fn]
        value_by_index = {i: value for value, i in function['arg_values'].items()}
        start, end, _ = parameters(lines[function['header']], fn)
        extra = ', '.join('i1 ' + names[(fn, value_by_index[i])] for i in sorted(indexes))
        changed[function['header']] = lines[function['header']][:end] + ', ' + extra + lines[function['header']][end:]
        for caller, location, _ in calls[fn]:
            _, end, _ = parameters(lines[location], fn)
            extra = ', '.join('i1 ' + flag(call_flags[(location, i)]) for i in sorted(indexes))
            changed[location] = lines[location][:end] + ', ' + extra + lines[location][end:]
            call_count += 1
    current = None
    for i, line in enumerate(lines):
        match = DEFINITION.match(line)
        if match:
            current = match[1]
        match = CMP.match(line)
        if match and 'null' in (match[4], match[5]):
            pointer = match[5] if match[4] == 'null' else match[4]
            value = flag(validity(current, pointer))
            changed[i] = '  ' + match[1] + ' = xor i1 ' + value + ', ' + ('true' if match[2] == 'eq' else 'false') + match[6]
    output = []
    for i, line in enumerate(lines):
        output += before.get(i, [])
        output.append(changed.get(i, line))
        output += after.get(i, [])
    output = '\n'.join(output)
    if spilling:
        from memory_effects import relax_private_volatile
        output = relax_private_volatile(output, spilling)
    if scan(output):
        raise ValueError('pointer/null compare remains')
    return output, dict(compares=len(matches), phis=sum(e[0] == 'phi' for e in expressions.values()),
                        selects=sum(e[0] == 'select' for e in expressions.values()),
                        threaded_parameters=sum(len(x) for x in needed.values()), calls=call_count)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    result, report = rewrite(args.source.read_text())
    with args.output.open('x') as stream:
        stream.write(result)
    print(json.dumps(report))
