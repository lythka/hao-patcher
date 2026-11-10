"""Relax invalid function/call memory promises after adding private volatile IO.

Clone affected attribute groups: unrelated functions retain their exact contract.
Propagate through direct callers, including callsite attributes. Reject indirect
references to affected functions rather than silently keeping a stale contract.
"""
import re
import null_compare

RESTRICTED = re.compile(r'\b(?:readnone|readonly|writeonly|argmemonly|inaccessiblememonly|inaccessiblemem_or_argmemonly|speculatable|nosync)\b')


def strip_restrictions(text):
    return re.sub(r'"(?:[^"\\]|\\.)*"|\b\w+\b',
                  lambda m: '' if RESTRICTED.fullmatch(m[0]) else m[0], text)


def relax_private_volatile(text, affected):
    lines = text.split('\n')
    functions, calls = null_compare.read_functions(lines)
    affected = set(affected)
    changed = True
    while changed:
        changed = False
        for callee in tuple(affected):
            for caller, _, _ in calls[callee]:
                if caller not in affected:
                    affected.add(caller)
                    changed = True
    locations = {functions[fn]['header'] for fn in affected}
    for fn in affected:
        locations.update(site[1] for site in calls[fn])
        token = re.compile(r'@' + re.escape(fn) + r'(?=[ (,)]|$)')
        for i, line in enumerate(lines):
            if token.search(line) and i not in locations and not line.startswith(('!', ';')):
                raise ValueError('indirect reference to volatile-lowered function: ' + fn)
    groups = {}
    for line in lines:
        match = re.fullmatch(r'attributes #(\d+) = (.*)', line)
        if match:
            groups[int(match[1])] = match[2]
    next_id = max(groups, default=-1) + 1
    clones = {}
    for i in sorted(locations):
        line = lines[i]
        # Restrict changes to the function/call suffix; preserve parameter attrs.
        name = (null_compare.DEFINITION.match(line) or null_compare.CALL.search(line))[1]
        _, end, _ = null_compare.parameters(line, name)
        prefix, suffix = line[:end + 1], line[end + 1:]
        if re.search(r'\bmemory\(', suffix):
            raise ValueError('unhandled modern memory attributes')
        suffix = strip_restrictions(suffix)
        def clone(match):
            nonlocal next_id
            old = int(match[1])
            body = groups[old]
            if 'memory(' in body:
                raise ValueError('unhandled modern memory attributes')
            stripped = strip_restrictions(body)
            if stripped == body:
                return match[0]
            if old not in clones:
                clones[old] = next_id
                next_id += 1
                lines.append(f'attributes #{clones[old]} = {stripped}')
            return '#' + str(clones[old])
        lines[i] = prefix + re.sub(r'#(\d+)', clone, suffix)
    return '\n'.join(lines)
