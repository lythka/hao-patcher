#!/usr/bin/env python3
"""Rewrite each distinct AIR module and splice it into the original MTLB container.

Ordinary entries round-trip via a single-function metallib. CI libraries share one
AIR module among many exported entries; assemble that whole module directly and
require every LLVM metadata record to remain byte-identical after disassembly.
Container metadata and HDYN/RLST payloads are retained, with offsets relocated.

Usage: build_metallib.py INPUT.metallib OUTDIR [--half-vector-only | --sampler-reads-only | --golden-gate | --legacy-sync-only] [--identity-control]
--half-vector-only runs only the half-vector integer pass (mkfix_half_vec_int), the pass used for
the Tahoe metallib repairs; --sampler-reads-only runs only the sampler-form read repair
(sampler_reads: read_sampler then texture_coords); --golden-gate adds the legacy
synchronization ABI repair and assembles directly. The default is the Tahoe
compatibility suite (compatibility.py).
--legacy-sync-only preserves unrelated shader code and repairs only the legacy
barrier/atomic ABI, using direct assembly and the same metadata checks.
"""
import hashlib
import json
from pathlib import Path
import re
import struct
import subprocess
import sys
import mtlb
import compatibility
fix = compatibility
import mkfix_half_vec_int
import sampler_reads
import golden_gate
import legacy_sync
from assemble_census import command as assembly_command
import null_compare


def sha(data):
    return hashlib.sha256(data).hexdigest()


def md_blob(data, lib, function, which):
    start, size = (lib['hdr'][3], lib['hdr'][4]) if which == 0 else (lib['hdr'][5], lib['hdr'][6])
    offsets = sorted({f['offt'][which] for f in lib['fns']} | {size})
    pos = function['offt'][which]
    if pos == size:
        return b''
    end = offsets[offsets.index(pos) + 1]
    return data[start + pos:start + end]


def same_metadata(a, b):
    if a == b:
        return True
    # Apple uses both inclusive and exclusive length words for these entries.
    return (len(a) == len(b) and len(a) >= 4 and a[4:] == b[4:]
            and struct.unpack_from('<I', a)[0] in (len(a), len(a) - 4)
            and struct.unpack_from('<I', b)[0] in (len(b), len(b) - 4))


def disassemble(path):
    result = subprocess.run(['xcrun', 'metal-objdump', '--disassemble', str(path)],
                            capture_output=True, text=True, check=True)
    assert '; ModuleID' in result.stdout, (path, 'missing AIR disassembly')
    return result.stdout[result.stdout.index('; ModuleID'):]


def metadata_graph(text, named_only=False):
    """Canonical reachable metadata, retaining distinct nodes and cyclic edges.

    metal-as drops unused TBAA after storage rewrites and renumbers the surviving
    nodes. Ignore only unreachable nodes; never ignore a changed reachable value.
    Quoted strings can contain !digits and are not references.
    """
    nodes, roots = {}, []
    token = re.compile(r'"(?:[^"\\]|\\.)*"|!\d+')
    for line in text.splitlines():
        match = re.fullmatch(r'!(\d+) = (.*)', line)
        if match:
            nodes[match[1]] = match[2]
        elif line.startswith('!'):
            roots.append(line)
        elif not named_only and not line.startswith(';'):
            roots.extend(m[0] for m in token.finditer(line) if m[0].startswith('!'))
    ids, bodies = {}, []

    def replace(match):
        value = match[0]
        if not value.startswith('!'):
            return value
        key = value[1:]
        if key not in ids:
            ids[key] = len(ids)
            index = ids[key]
            bodies.append(None)
            bodies[index] = token.sub(replace, nodes[key])
        return '!' + str(ids[key])

    return [token.sub(replace, root) for root in roots], bodies


def assembled_module(path, original, name, rewritten=None):
    assembled = disassemble(path)
    if rewritten is None:
        old_metadata = [line for line in original.splitlines() if line.startswith('!')]
        new_metadata = [line for line in assembled.splitlines() if line.startswith('!')]
        assert old_metadata == new_metadata, (name, 'assembled module metadata changed')
    else:
        expected = golden_gate.defer_renderbox_blend_reference(original)[0] if fix is golden_gate else original
        assert metadata_graph(expected, True) == metadata_graph(rewritten, True), (name, 'public metadata changed')
        assert metadata_graph(rewritten) == metadata_graph(assembled), (name, 'reachable metadata changed')
    fix.refuse_bad_state(assembled)
    return path.read_bytes()


def run(command):
    result = subprocess.run(command, capture_output=True, text=True)
    assert result.returncode == 0, (command, result.stderr[:1200])


def definition_changes(original, rewritten):
    def definitions(text):
        lines = text.split('\n')
        functions, _ = null_compare.read_functions(lines)
        return {name: '\n'.join(lines[fn['header']:fn['end'] + 1])
                for name, fn in functions.items()}, functions
    before, functions = definitions(original)
    after, _ = definitions(rewritten)
    from inline_private import public_surface
    assert public_surface(original) == public_surface(rewritten), 'public ABI changed'
    assert set(after) <= set(before), 'new IR definitions appeared'
    removed = set(before) - set(after)
    assert all(functions[key]['internal'] for key in removed), 'public definition removed'
    altered = {key for key in after if before[key] != after[key]}
    return dict(removed_internal_definitions=sorted(removed), functions=sorted(altered),
                unchanged_definition_text_count=len(after) - len(altered),
                unchanged_definitions_equal=True)


def main():
    global fix
    fix = compatibility
    source, output = Path(sys.argv[1]), Path(sys.argv[2])
    if '--half-vector-only' in sys.argv:
        fix = mkfix_half_vec_int
    if '--sampler-reads-only' in sys.argv:
        fix = sampler_reads
    if '--golden-gate' in sys.argv:
        fix = golden_gate
    if '--legacy-sync-only' in sys.argv:
        fix = legacy_sync
    direct_assembly = fix in (golden_gate, legacy_sync)
    identity = '--identity-control' in sys.argv
    output.mkdir(parents=True)
    data = source.read_bytes()
    lib = mtlb.check(data)
    groups = {}
    for function in lib['fns']:
        groups.setdefault(function['offt'][2], []).append(function)
    replacements, modules = {}, []
    for index, aliases in enumerate(groups.values()):
        function = aliases[0]
        name = function['name']
        safe = '%03d-' % index + name.replace('/', '_').replace(':', '_')[:120]
        directory = output / 'functions' / safe
        directory.mkdir(parents=True)
        blob = mtlb.bitcode(data, lib, function)
        (directory / 'original.air').write_bytes(blob)
        original = disassemble(directory / 'original.air')
        (directory / 'original.ll').write_text(original)
        rewritten, report = fix.rewrite(original)
        changed = rewritten != original
        if identity and changed:
            rewritten = original + '\n; identity round trip (control)\n'
        row = dict(function=name, aliases=[f['name'] for f in aliases],
                   original_sha256=sha(blob), changed=changed, **report)
        row.update(definition_changes(original, rewritten))
        if changed:
            if len(aliases) > 1 and not re.search(r'^source_filename =', rewritten, re.M):
                rewritten = rewritten.replace('target datalayout =', 'source_filename = \"air-module-' + sha(blob) + '.air\"\n' + 'target datalayout =', 1)
                row['stable_source_filename'] = True
            (directory / 'fixed.ll').write_text(rewritten)
            # The frontend upgrades old intrinsic ABIs and can narrow repaired
            # texture coordinates again. Keep the exact rewritten AIR on GG.
            args = (['xcrun', 'metal-as', str(directory / 'fixed.ll'), '-o', str(directory / 'fixed.air')]
                    if direct_assembly else
                    assembly_command(rewritten, directory / 'fixed.ll', directory / 'fixed.air'))
            row['assembly_command'] = args
            run(args)
            if len(aliases) > 1 or args[1] == 'metal-as':
                new_blob = assembled_module(directory / 'fixed.air', original, name,
                                            rewritten if direct_assembly else None)
                row['shared_module_metadata_equal'] = True
            else:
                run(['xcrun', 'metallib', str(directory / 'fixed.air'), '-o', str(directory / 'single.metallib')])
                single = (directory / 'single.metallib').read_bytes()
                linked = mtlb.check(single)
                found = [f for f in linked['fns'] if f['name'] == name]
                assert len(found) == 1, (name, 'linked entry missing or ambiguous')
                linked_metadata_equal = True
                for which, label in enumerate(('public', 'private')):
                    a = md_blob(data, lib, function, which)
                    b = md_blob(single, linked, found[0], which)
                    row[label + '_md_equal'] = a == b
                    row[label + '_md_body_equal'] = same_metadata(a, b)
                    linked_metadata_equal &= same_metadata(a, b)
                if linked_metadata_equal:
                    new_blob = mtlb.bitcode(single, linked, found[0])
                else:
                    # The linker can omit factory function-constant records even
                    # on an identity round trip. Keep the original container and
                    # use assembled AIR only when its complete metadata is exact.
                    new_blob = assembled_module(directory / 'fixed.air', original, name)
                    row['preserved_factory_metadata'] = True
                    row['assembled_module_metadata_equal'] = True
                emitted = directory / 'emitted.air'
                emitted.write_bytes(new_blob)
                fix.refuse_bad_state(disassemble(emitted))
                row['emitted_patterns_absent'] = True
            for alias in aliases:
                replacements[alias['name']] = new_blob
            row['fixed_sha256'] = sha(new_blob)
        modules.append(row)
    new_data = mtlb.splice(data, replacements)
    (output / 'default.metallib').write_bytes(new_data)
    report = dict(input=str(source), input_sha256=sha(data), output=str(output / 'default.metallib'),
                  output_sha256=sha(new_data), changed=list(replacements),
                  rewritten_definitions=sorted({name for row in modules if row['changed'] for name in row['functions']}),
                  functions=modules)
    (output / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({key: report[key] for key in ('input_sha256', 'output_sha256', 'rewritten_definitions')}, indent=1))


if __name__ == '__main__':
    main()
