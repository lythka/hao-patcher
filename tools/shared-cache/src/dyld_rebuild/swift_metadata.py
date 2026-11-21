"""Regenerate Swift value metadata, relocate its closure, and register the merged table.

Names are the recipe. Runtime captures are build inputs, never cross-build layout
templates. References use image UUID and section identity rather than old cache
addresses; process-local references must be dependencies in the same catalog.
"""
import argparse
from bisect import bisect_right
from datetime import datetime, timezone
import json
from pathlib import Path
import struct
import subprocess
import uuid

from .cache import commands
from .exports import require
from .host_tools import compile_adapter
from .image_indexes import swift_roots
from .loader_generation import LoaderFamily
from .objc_emit import FamilyReader
from .run_inputs import pin
from .swift_refs import PRESPECIALIZATION_ABIS, read_table


ROOT = Path(__file__).resolve().parents[2]
FUNCTION_SLOTS = (0, 1, 2, 3, 4, 5, 6, 7, 11, 12, 13)
RECORD_SIZE = 160


def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')


def revision():
    """The unit's revision: the newest line of README.md's Revisions (a constant here went stale at 75)."""
    import re
    return int(re.search(r'^## Revisions\n+- r(\d+) ', (ROOT / 'README.md').read_text(), re.M).group(1))


def provenance(inputs, **fields):
    from .source_identity import head, content_hash
    return dict(unit='cache/shared-cache', revision=revision(), git_commit=head(ROOT), git_dirty=None,
                source_sha256=content_hash(ROOT),
                date=datetime.now(timezone.utc).isoformat(), inputs=[pin(p) for p in inputs], **fields)


class References:
    """Only file-backed Mach-O sections can supply reusable pointer identities."""
    def __init__(self, family):
        self.ranges, self.sections = [], {}
        for path, header in family.images:
            cmds = commands(family, header)
            image_uuid = next(bytes(raw[8:24]).hex() for _, cmd, raw in cmds if cmd == 0x1b)
            for _, cmd, raw in cmds:
                if cmd != 0x19:
                    continue
                protection = struct.unpack_from('<I', raw, 60)[0]
                for i in range(struct.unpack_from('<I', raw, 64)[0]):
                    section = raw[72 + i * 80:152 + i * 80]
                    name, segment, address, size = struct.unpack_from('<16s16sQQ', section)
                    section_type = struct.unpack_from('<I', section, 64)[0] & 255
                    # Swift allocates metadata in libswiftCore's zero-fill pool.
                    # Those addresses are process-local, despite being inside
                    # both the shared-cache reservation and an image section.
                    if not size or section_type in (1, 12, 18):
                        continue
                    name, segment = name.rstrip(b'\0').decode(), segment.rstrip(b'\0').decode()
                    identity = (path, segment, name)
                    row = (address, address + size, identity, image_uuid, protection)
                    self.ranges.append(row)
                    require(identity not in self.sections, 'ambiguous Swift section identity')
                    self.sections[identity] = row
        self.ranges.sort()
        self.starts = [r[0] for r in self.ranges]

    def encode(self, address, *, executable=False):
        index = bisect_right(self.starts, address) - 1
        require(index >= 0, 'Swift reference has no image section')
        lo, hi, identity, image_uuid, protection = self.ranges[index]
        require(lo <= address < hi, 'Swift reference is process-local or outside image sections')
        require(not executable or protection & 4, 'Swift witness is not executable')
        return dict(image=identity[0], segment=identity[1], section=identity[2],
                    image_uuid=image_uuid, offset=address - lo, executable=executable)

    def decode(self, ref):
        lo, hi, _, image_uuid, protection = self.sections[(ref['image'], ref['segment'], ref['section'])]
        require(ref['image_uuid'] == image_uuid, 'Swift runtime capture belongs to a different Mach-O build: ' + ref['image'])
        offset = ref['offset']
        require(type(offset) is int and 0 <= offset < hi - lo, 'Swift section reference overflow')
        require(not ref['executable'] or protection & 4, 'Swift witness lost executable mapping')
        return lo + offset


def recipe_rows(recipe):
    require(recipe.get('schema') == 1, 'unknown Swift recipe schema')
    rows = recipe['records']
    require(len({r.get('id', r['name']) for r in rows}) == len(rows), 'duplicate Swift recipe')
    for row in rows:
        require(row['owner'].startswith('/') and all(c not in row['name'] + row['owner'] for c in '\n\r\t\0'),
                'invalid Swift recipe name/owner')
    return rows


def recipe_text(recipe, graph):
    lines = []
    if recipe.get('cache_uuid'):
        lines.append('@cache\t' + str(uuid.UUID(recipe['cache_uuid'])))
    if recipe.get('installed_metadata'):
        require(graph and recipe.get('cache_uuid'), 'exact-key installed metadata needs a cache UUID')
        for offset in recipe['installed_metadata']:
            require(type(offset) is int and offset >= 0, 'invalid installed metadata offset')
            lines.append('@metadata\t' + format(offset, 'x'))
    if graph:
        lines.extend('@load\t' + path for path in recipe.get('loads', []))
    defined = set()
    for node in recipe.get('definitions', []):
        require(graph and recipe.get('cache_uuid'), 'constructor recipes need a cache UUID')
        identity = node['id']
        require(identity.isdecimal() and identity not in defined, 'invalid constructor identity')
        if node['operation'] == 'resolve':
            require(all(c not in node['name'] for c in '\n\r\t\0'), 'invalid constructor name')
            lines.append('@type\t'+identity+'\t'+node['name'])
        elif node['operation'] == 'reference':
            require(type(node['metadata']) is int and node['metadata'] >= 0, 'invalid metadata reference')
            lines.append('@reference\t'+identity+'\t'+format(node['metadata'],'x'))
        elif node['operation'] == 'generic':
            require(node['arguments'] and all(k in defined for k in node['arguments']),
                    'constructor dependency is not defined before parent')
            lines.append('@generic\t'+identity+'\t'+format(node['descriptor'],'x')+'\t'+','.join(node['arguments']))
        elif node['operation'] == 'opaque':
            require(all(k in defined for k in node['arguments']), 'opaque dependency is not defined before parent')
            requirements = ','.join(f"{r['subject_address']:x}:{r['subject_length']}:{r['protocol']:x}"
                                    for r in node['requirements']) or '-'
            lines.append('\t'.join(('@opaque', identity, format(node['descriptor'], 'x'), format(node['context'], 'x'), str(node['ordinal']),
                                    str(node['key_arguments']), ','.join(node['arguments']) or '-', requirements)))
        else:
            raise ValueError('unknown Swift constructor operation')
        defined.add(identity)
    for row in recipe_rows(recipe):
        line = row['name'] + '\t' + row['owner']
        if 'dependency' in row:
            require(row['dependency'] in defined, 'root constructor is not defined')
            line += '\t&'+row['dependency']
        elif 'key' in row:
            require(graph and recipe.get('cache_uuid') and 2<=len(row['key'])<=64 and
                    all(type(p) is int and p>=0 for p in row['key']), 'invalid exact-key verification recipe')
            line += '\t' + ','.join(format(p,'x') for p in row['key'])
        lines.append(line)
    return '\n'.join(lines) + '\n'


def normalize(recipe, captured, family):
    rows = recipe_rows(recipe)
    header, *observations = captured
    require(header['schema'] == 1 and header['cache_uuid'] == str(uuid.UUID(bytes=bytes(family.main.data[88:104]))),
            'Swift capture did not run with the supplied cache')
    require(len(observations) == len(rows), 'Swift capture did not regenerate every requested entry')
    base = family.main.mappings[0].address
    slide = header['base'] - base
    references = References(family)
    owned = {row['metadata']: recipe['name'] for row, recipe in zip(observations, rows)}
    require(len(owned) == len(rows), 'two Swift recipe names resolved to the same metadata')
    result = []
    for index, (requested, observed) in enumerate(zip(rows, observations), 1):
        require(observed['index'] == index and observed['none_copy_destroy'] is True,
                'Swift capture order or value test failed')
        m, w = observed['full_metadata'], observed['witness']
        require(len(m) == 6 and len(w) == 14 and m[0] == m[5] == 0 and m[2] == 0x202,
                'unsupported captured Swift metadata shape')
        require(w[8] <= w[9] and w[9] > 0 and w[10] & 0x200000 and not w[10] & 0x800000,
                'incomplete captured Swift value layout')
        argument = ({'owned': owned[m[4]]} if m[4] in owned else references.encode(m[4] - slide))
        result.append(dict(name=requested['name'], descriptor=references.encode(m[3] - slide), argument=argument,
                           witness_data=w[8:11],
                           functions={str(slot): references.encode(w[slot] - slide, executable=True)
                                      for slot in FUNCTION_SLOTS}))
    return dict(schema=1, kind='swift-optional-catalog', source_cache_uuid=header['cache_uuid'], records=result)


def capture(cache, recipe_path, output, runner=None, captured=None, prepare_only=False, base_catalog=None):
    output = Path(output)
    output.mkdir(parents=True)
    recipe = json.loads(Path(recipe_path).read_text())
    from .metadata_graph import KIND, MetadataGraph
    family = LoaderFamily(cache)
    try:
        if recipe.get('kind') == KIND:
            write_json(output / 'graph.json', recipe)
            recipe = MetadataGraph(recipe).bind(family)
        elif recipe.get('cache_uuid'):
            # Old exact-key verification/capture artifacts are bound evidence.
            # Replacing their UUID would reuse stale constructor offsets.
            require(str(uuid.UUID(recipe['cache_uuid'])) == str(uuid.UUID(bytes=bytes(family.main.data[88:104]))),
                    'bound recipe and target cache differ; use a portable graph or regenerate installed keys')
    finally:
        family.close()
    rows = recipe_rows(recipe)
    graph = recipe.get('kind') == 'generic-values'
    require(not graph or base_catalog is not None, 'generic extension needs --base-catalog to retain existing entries')
    if graph and recipe.get('discover_all_modules'):
        from .swift_graph import framework_owners
        family = LoaderFamily(cache)
        try:
            recipe['owners'] = framework_owners(family)
        finally:
            family.close()
    if base_catalog:
        (output / 'base-catalog.json').write_bytes(Path(base_catalog).read_bytes())
    write_json(output / 'recipe.json', recipe)
    write_json(output / 'capture-inputs.json', dict(cache=str(Path(cache).resolve()), graph=graph))
    recipe_input = output / 'names.tsv'
    recipe_input.write_text(recipe_text(recipe, graph))
    raw = output / 'capture.jsonl'
    source = ROOT / ('tools/swift-graph.c' if graph else 'tools/swift-capture.c')
    inputs = [Path(recipe_path), source, Path(__file__).with_name('metadata_graph.py'), Path(__file__).with_name('swift_abi.py')] + ([Path(base_catalog)] if base_catalog else [])
    if graph:
        inputs.append(ROOT / "tools/cache-address.h")
    if captured is None:
        binary = output / 'swift-capture'
        from .helper_build import probe
        probe('swift-graph' if graph else 'swift-capture', binary)
        if prepare_only:
            family = LoaderFamily(cache)
            try:
                write_json(output / 'build.json', provenance(inputs + [f.path for f in family.files],
                           operation='swift-capture-bundle', records=len(rows),
                           cache_uuid=str(uuid.UUID(bytes=bytes(family.main.data[88:104])))))
            finally:
                family.close()
            return output
        # Runner contract: one executable argument, recipe on stdin, JSONL on
        # stdout, diagnostics on stderr, nonzero status for any failed record.
        # The caller owns transport and the target session; no implicit SSH/boot.
        command = [str(binary.resolve())] if runner is None else [str(Path(runner).resolve()), str(binary.resolve())]
        with recipe_input.open('rb') as source, raw.open('wb') as dest, (output / 'capture.log').open('wb') as log:
            subprocess.run(command, stdin=source, stdout=dest, stderr=log, check=True)
        if runner:
            inputs.append(Path(runner))
    else:
        raw.write_bytes(Path(captured).read_bytes())
    family = LoaderFamily(cache)
    try:
        observations = [json.loads(line) for line in raw.read_text().splitlines()]
        if graph:
            from .swift_graph import normalize_graph, verification_recipe
            catalog, coverage = normalize_graph(recipe, observations, family, json.loads(Path(base_catalog).read_text()))
            write_json(output / 'coverage.json', coverage)
            # Use all catalog names, including retained entries, on the verification
            # boot. Payload image owners make the existing Optional roots resolvable.
            write_json(output / 'verification-recipe.json', verification_recipe(catalog))
            print(f"Swift extension: {coverage['added']} added, {coverage['retained']} retained; "
                  f"{coverage['included']} catalog roots, {coverage.get('compiler_provided',0)} compiler-provided, "
                  f"{coverage['requested']} requested; see {output / 'coverage.json'}")
        else:
            catalog = normalize(recipe, observations, family)
        inputs += [f.path for f in family.files]
        inputs.append(raw)
        write_json(output / 'catalog.json', catalog)
        write_json(output / 'build.json', provenance(inputs, operation='swift-runtime-generation',
                   records=len(catalog['records']), cache_uuid=catalog['source_cache_uuid']))
    finally:
        family.close()
    return output / 'catalog.json'


def pointer_entries(family):
    base, roots = swift_roots(family)
    reader = FamilyReader(family)
    data = base + roots['prespecialization']
    require(struct.unpack('<II', family.read(data, 8)) in PRESPECIALIZATION_ABIS, 'unsupported Swift prespecialization ABI')
    # v1.4 (26) and v1.5 (27, 26B131) have the pointer-keyed metadata map at +24. v1.5 adds a second,
    # empty pointer-keyed table at +48 (dyld slot 0 on 26B131, so the metadata map is slot 1); other
    # pointer tables must retain their own roots.
    table = reader.ptr(data + 24)
    require(table - base in roots['pointer_tables'], 'Swift and dyld table roots disagree')
    decoded = read_table(family.read, table, 'pointer')
    values = {r[0]: r[3] & ((1 << 63) - 1) for r in decoded.rows}
    keys = dict(decoded.pointer_keys)
    entries = {keys[group[0]]: [values[p] for p in group] for group in decoded.groups}
    header = base + struct.unpack_from('<Q', family.main.data, 376)[0]
    slot = roots['pointer_tables'].index(table - base)
    return entries, dict(base=base, table=table, swift_pointer=data + 24, dyld_offset=header + 40 + slot * 8)


def materialize(catalog, family, address):
    if catalog.get('schema') in (2, 3) and catalog.get('kind') == 'swift-value-catalog':
        from .swift_graph import materialize_graph
        return materialize_graph(catalog, family, address)
    require(catalog.get('schema') == 1 and catalog.get('kind') == 'swift-optional-catalog', 'unknown Swift catalog')
    references = References(family)
    entries, roots = pointer_entries(family)
    base = roots['base']
    original = {key: list(value) for key, value in entries.items()}
    pending = {r['name']: r for r in catalog['records']}
    require(pending and len(pending) == len(catalog['records']), 'empty or duplicate Swift catalog')
    resolved, records, fields, data = {}, [], [], bytearray()
    while pending:
        ready = [name for name, row in pending.items() if 'owned' not in row['argument'] or row['argument']['owned'] in resolved]
        require(ready, 'cyclic or missing Swift owned dependency')
        for name in ready:
            row = pending.pop(name)
            descriptor = references.decode(row['descriptor'])
            argument = (resolved[row['argument']['owned']] if 'owned' in row['argument'] else references.decode(row['argument']))
            key = (descriptor - base, argument - base)
            if key in entries:
                require(len(entries[key]) == 1, 'ambiguous existing Swift metadata identity')
                resolved[name] = base + entries[key][0]
                records.append(dict(name=name, key=list(key), address=resolved[name], existing=True))
                continue
            start = len(data)
            witness_data = row['witness_data']
            require(len(witness_data) == 3 and 0 <= witness_data[0] <= witness_data[1] and witness_data[1] > 0,
                    'invalid Swift witness extent')
            words = [0] * 20
            words[2] = 0x202
            words[14:17] = witness_data
            data.extend(struct.pack('<20Q', *words))
            def pointer(offset, target):
                fields.append(dict(address=address + start + offset, target=target))
            pointer(8, address + start + 48)
            pointer(24, descriptor)
            pointer(32, argument)
            require(set(row['functions']) == {str(i) for i in FUNCTION_SLOTS}, 'incomplete Swift value witnesses')
            for slot in FUNCTION_SLOTS:
                pointer(48 + slot * 8, references.decode(row['functions'][str(slot)]))
            resolved[name] = address + start + 16
            entries[key] = [resolved[name] - base]
            records.append(dict(name=name, key=list(key), address=resolved[name], existing=False))
    return bytes(data), fields, entries, roots, records, original


def table_reservations(catalog, entries, directory):
    """Bound table sizes from key arities and Apple's writer, before relocation.

    Counting retained aliases twice is conservative; pointer values cannot grow
    the writer beyond its maximum hash capacity and these key/value extents.
    """
    counts = dict(metadata=[len(entries), sum(map(len, entries)), sum(map(len, entries.values()))],
                  witness=[0, 0, 0], metatype=[0, 0, 0])
    for kind, rows in (('metadata', catalog['records']), ('witness', catalog.get('witnesses', []))):
        for row in rows:
            table = 'metatype' if row.get('record_kind') == 'metatype' else kind
            counts[table][0] += 1
            counts[table][1] += len(row['key']) if 'key' in row else 2
            counts[table][2] += 1
    # Every normalized key has the same arity/value extent as its exact key.
    if any('type_arguments' in r and len(r['key'])>r['type_arguments']+1 for r in catalog['records']):
        counts['normalized'] = list(counts['metadata'])
    directory = Path(directory)
    binary = compile_adapter(directory / 'writer', 'swift-table-writer', [ROOT / 'tools/swift-table-writer.cpp'])
    sizes = {}
    for kind, (keys, words, values) in counts.items():
        if keys:
            result = subprocess.run([str(binary), '--reserve', str(keys), str(words), str(values)],
                                    capture_output=True, text=True, check=True)
            sizes[kind] = json.loads(result.stdout)['bytes']
    write_json(directory / 'reservations.json', dict(counts=counts, bytes=sizes))
    return sizes


def emit_table(entries, directory):
    directory = Path(directory)
    directory.mkdir(parents=True)
    binary = compile_adapter(directory / 'writer', 'swift-table-writer', [ROOT / 'tools/swift-table-writer.cpp'])
    raw = bytearray(struct.pack('<II', 0x31545753, len(entries)))
    for key, values in sorted(entries.items()):
        raw += struct.pack('<II', len(key), len(values))
        raw += struct.pack(f'<{len(key)}Q', *key) + struct.pack(f'<{len(values)}Q', *values)
    source, dest = directory / 'table.input', directory / 'table.bin'
    source.write_bytes(raw)
    result = subprocess.run([str(binary), str(source), str(dest)], capture_output=True, text=True, check=True)
    blob = dest.read_bytes()
    decoded = read_table(lambda a, n: blob[a:a + n], 0, 'pointer')
    values = {r[0]: r[3] & ((1 << 63) - 1) for r in decoded.rows}
    keys = dict(decoded.pointer_keys)
    actual = {keys[g[0]]: [values[p] for p in g] for g in decoded.groups}
    require(actual == entries, 'Swift emitted table failed independent decode')
    return blob, json.loads(result.stdout)


def prepare(catalog, family, layout, output, runtime_toolchain=None):
    from .witness_initializers import normalize
    catalog,initializers=normalize(catalog,family)
    if any(r.get('record_kind')=='metatype' for r in catalog['records']):
        require(catalog.get('kind')=='swift-dependency-catalog' and runtime_toolchain is not None,
                'metatype catalog requires the dependency installer and source-built runtime toolchain')
    if catalog.get('kind') == 'swift-dependency-catalog' and not catalog['witnesses'] and not any(
            r.get('record_kind')=='metatype' for r in catalog['records']):
        catalog=dict(catalog,schema=3,kind='swift-value-catalog')
    if catalog.get('kind') == 'swift-dependency-catalog' and runtime_toolchain:
        from .swift_dependency_install import prepare as prepare_dependencies
        result=prepare_dependencies(catalog,family,layout,Path(output),Path(runtime_toolchain))
        result['witness_initializers']=initializers
        return result
    data, fields, entries, roots, records, original = materialize(catalog, family, layout['swift_metadata']['address'])
    table, lookup = emit_table(entries, output)
    address = layout['swift_table']['address']
    blobs = [dict(reservation='swift_table', address=address, data=table.hex())]
    if data:
        blobs.append(dict(reservation='swift_metadata', address=layout['swift_metadata']['address'], data=data.hex()))
    for row in blobs:
        require(len(bytes.fromhex(row['data'])) <= layout[row['reservation']]['reserve'], 'Swift allocation too small')
    return dict(schema=1, blobs=blobs, new_pointer_fields=fields, records=records,
                retained_entries=[dict(key=list(k), values=v) for k, v in sorted(original.items())],
                original_keys=len(original), total_keys=len(entries), source_lookup=lookup,
                pointer_edits=[dict(address=roots['swift_pointer'], expected=roots['table'], target=address,
                                    member_before=True, member_after=True)],
                byte_edits=[dict(address=roots['dyld_offset'],
                                 before=struct.pack('<Q', roots['table'] - roots['base']).hex(),
                                 after=struct.pack('<Q', address - roots['base']).hex())])


def capture_main(argv):
    parser = argparse.ArgumentParser(description='Regenerate Swift layouts on the matching native runtime')
    parser.add_argument('cache', type=Path)
    parser.add_argument('recipe', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--runner', type=Path)
    parser.add_argument('--base-catalog', type=Path, help='existing catalog to retain while extending generic value coverage')
    action = parser.add_mutually_exclusive_group()
    action.add_argument('--captured', type=Path, help='normalize an already completed native JSONL capture')
    action.add_argument('--prepare-only', action='store_true', help='build a signed bundle for netboot/bench --swift-metadata')
    args = parser.parse_args(argv)
    print(capture(args.cache, args.recipe, args.output, args.runner, args.captured, args.prepare_only, args.base_catalog))
