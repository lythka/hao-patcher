"""Expose closed internal private-storage paths using pinned LLVM passes.

Only called after local lowering leaves dynamic private half2 loads. Select by
those load sites and direct call graph, never by shader/function names. Refuse
recursion, function-pointer escape, noinline contracts and unbounded expansion.
LLVM performs inlining/SROA; the usual storage-use proof must still succeed.
"""
from pathlib import Path
import re
import subprocess
import tempfile
import half2_load
import null_compare


PASSES = 'always-inline,function(sroa,mem2reg),globaldce'
MAX_INPUT = 8 * 1024 * 1024
MAX_OUTPUT = 32 * 1024 * 1024
MAX_FUNCTIONS = 128


def attribute_tokens(text):
    return list(re.finditer(r'"(?:[^"\\]|\\.)*"|[^"\s]+', null_compare.without_comment(text)))


def attribute_groups(text):
    return [token for token in attribute_tokens(text) if re.fullmatch(r'#\d+', token[0])]


def public_surface(text):
    lines = text.split('\n')
    functions, _ = null_compare.read_functions(lines)
    result = {}
    for name, fn in functions.items():
        if not fn['internal']:
            line = lines[fn['header']]
            _, end, args = null_compare.parameters(line, name)
            result[name] = (line[:line.index('@' + name)], tuple(args))
    return result


def selected_functions(text):
    lines = text.split('\n')
    functions, calls = null_compare.read_functions(lines)
    seeds = {row['function'] for row in half2_load.scan(text)}
    selected, pending = set(seeds), list(seeds)
    while pending:
        for caller, _, _ in calls[pending.pop()]:
            if caller not in selected:
                selected.add(caller); pending.append(caller)
    edges = {name: set() for name in functions}
    for callee, sites in calls.items():
        if callee in functions:
            for caller, _, _ in sites:
                edges[caller].add(callee)
    pending = list(selected)
    while pending:
        for child in edges[pending.pop()]:
            if functions[child]['internal'] and child not in selected:
                selected.add(child); pending.append(child)
    selected = {name for name in selected if functions[name]['internal']}
    if not selected:
        raise ValueError('private-load fallback has no closed internal helpers')
    if len(selected) > MAX_FUNCTIONS:
        raise ValueError('private helper graph exceeds bound')
    visiting, visited = set(), set()

    def visit(name):
        if name in visiting:
            raise ValueError('recursive private helper graph')
        if name in visited:
            return
        visiting.add(name)
        for child in edges[name] & selected:
            visit(child)
        visiting.remove(name); visited.add(name)

    groups = {int(m[1]): m[2] for line in lines if (m := re.fullmatch(r'attributes #(\d+) = (.*)', line))}
    for name in selected:
        visit(name)
        allowed = {functions[name]['header']} | {site[1] for site in calls[name]}
        token = re.compile('@' + re.escape(name) + r'(?=[ (,)]|$)')
        for i, line in enumerate(lines):
            if token.search(null_compare.without_comment(line)) and i not in allowed and not line.startswith(('!', ';')):
                raise ValueError('private helper address escapes: ' + name)
        header = lines[functions[name]['header']]
        _, end, _ = null_compare.parameters(header, name)
        suffix = null_compare.without_comment(header[end + 1:])
        references = [int(token[0][1:]) for token in attribute_groups(suffix)]
        if any(n not in groups for n in references):
            raise ValueError('missing function attribute group')
        attributes = suffix + ' ' + ' '.join(null_compare.without_comment(groups[n]) for n in references)
        tokens = [token[0] for token in attribute_tokens(attributes)]
        if any(token in ('noinline', 'optnone') for token in tokens):
            raise ValueError('private helper has noinline/optnone contract')
    return lines, functions, selected


def rewrite(text):
    if len(text.encode()) > MAX_INPUT:
        raise ValueError('private helper module exceeds input bound')
    lines, functions, selected = selected_functions(text)
    for name in selected:
        location = functions[name]['header']
        header = lines[location]
        _, end, _ = null_compare.parameters(header, name)
        groups = attribute_groups(header[end + 1:])
        if not groups:
            # Do not guess placement around unfamiliar section/personality syntax.
            raise ValueError('private helper lacks a canonical attribute group')
        location_in_header = end + 1 + groups[0].start()
        lines[location] = header[:location_in_header] + 'alwaysinline ' + header[location_in_header:]
    with tempfile.TemporaryDirectory(prefix='shader-private-inline-') as directory:
        source = Path(directory) / 'marked.ll'
        output = Path(directory) / 'inlined.ll'
        source.write_text('\n'.join(lines))
        result = subprocess.run(['xcrun', 'metal-opt', '-S', '-passes=' + PASSES,
                                 str(source), '-o', str(output)], capture_output=True, text=True, timeout=90)
        if result.returncode:
            raise ValueError('private helper LLVM pass refused: ' + result.stderr[:1200])
        if output.stat().st_size > MAX_OUTPUT:
            raise ValueError('private helper expansion exceeds output bound')
        rewritten = output.read_text()
    scalar = re.compile(r'^  (%[-\w.$]+) = load(?: volatile)? half, half\* (%[-\w.$]+), align (\d+)(.*)$')
    if half2_load.scan(rewritten, scalar):
        raise ValueError('dynamic private scalar-half load after inlining')
    if public_surface(text) != public_surface(rewritten):
        raise ValueError('private helper pass changed public ABI')
    # Preserve the original diagnostic identity, avoiding temporary paths in output.
    original_id = next((line for line in text.splitlines() if line.startswith('; ModuleID')), '; ModuleID = \'private-helper\'')
    rewritten = re.sub(r'^; ModuleID[^\n]*', lambda _: original_id, rewritten, count=1, flags=re.M)
    return rewritten, dict(functions=sorted(selected), passes=PASSES,
                           input_bytes=len(text.encode()), output_bytes=len(rewritten.encode()))
