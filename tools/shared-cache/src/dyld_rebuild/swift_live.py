"""Analyze live Swift dependencies through the upstream Remote Mirror API.

Swift owns type decoding. This module normalizes identities and retains unresolved
observations; reflection is not a metadata serializer or an adoption test.
"""
import argparse
from bisect import bisect_right
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import uuid

from .exports import Refusal, require
from .swift_metadata import References, provenance, write_json

ROOT = Path(__file__).resolve().parents[2]
KINDS = ('unknown', 'builtin', 'raw_pointer', 'tuple', 'struct', 'no_payload_enum',
         'single_payload_enum', 'multi_payload_enum', 'thick_function',
         'opaque_existential', 'class_existential', 'error_existential',
         'existential_metatype', 'strong_reference', 'unowned_reference',
         'weak_reference', 'unmanaged_reference', 'class_instance',
         'closure_context', 'array')


def sources():
    return [Path(__file__), ROOT/'tools/swift-reflection-reader.c',
            *sorted((ROOT/'vendor/swift-remote-mirror').iterdir())]


def prepare(output, arch='x86_64'):
    output.mkdir(parents=True, exist_ok=False)
    from .helper_build import probe
    require(arch in ('arm64', 'x86_64'), 'unsupported reflection reader architecture')
    binary = probe('swift-reflection-reader', output/'swift-reflection-reader')
    write_json(output/'build.json', provenance(sources()+[binary], operation='swift-reflection-reader',
                                              architecture=arch))


class Identities:
    def __init__(self, refs, identity, images):
        self.refs, self.slide = refs, identity['cache_slide']
        self.ranges = sorted(((s['start'], s['start']+s['size'], image, s['name'])
                              for image in images if image['path'] not in refs.images
                              for s in image['segments']), key=lambda r: (r[0], r[1], r[2]['path'], r[3]))
        self.starts = [r[0] for r in self.ranges]

    def reference(self, address):
        if not address:
            return None
        try:
            return self.refs.encode(address-self.slide)
        except Refusal:
            pass
        i = bisect_right(self.starts, address)-1
        if i >= 0:
            lo, hi, image, segment = self.ranges[i]
            # Only app/non-cache images use segment identities. The cache's
            # zero-fill metadata pools must not become reusable file references.
            if lo <= address < hi and image['path'] not in self.refs.images:
                return dict(image=image['path'], image_uuid=image['uuid'],
                            segment=segment, offset=address-lo)
        return None

    def name(self, name):
        if not name:
            return None, ['missing_name']
        blockers = []
        # Remote Mirror emits anonymous descriptor addresses in these names.
        # Replace their complete length-prefixed components with file identities;
        # this is an identity string, never a mangling submitted to Swift.
        for match in list(re.finditer(r'\$([0-9a-fA-F]{6,16})(?=LL)', name)):
            token = match[0]
            ref = self.reference(int(match[1], 16))
            if ref is None:
                blockers.append('process_local_context')
            elif str(len(token))+token in name:
                name = name.replace(str(len(token))+token,
                                    '{context:'+json.dumps(ref, sort_keys=True, separators=(',', ':'))+'}')
            else:
                blockers.append('unrecognized_context_name')
        return (None if blockers else name), blockers


def normalize(rows, inventory, refs):
    identity = next(r for r in rows if 'pid' in r and 'cache_uuid' in r)
    end = rows[-1] if rows else {}
    require(end.get('complete'), 'reflection capture is incomplete')
    expected = [r for block in inventory for r in block.get('metadata', [])]
    require(all(block['pid'] == identity['pid'] for block in inventory),
            'inventory belongs to another process')
    roots = [dict(r) for r in rows if 'root' in r]
    nodes = {r['node']: dict(r) for r in rows if 'node' in r}
    require(len(roots) == end['roots'] == len(expected), 'reflection root count differs')
    require(len(nodes) == end['nodes'], 'reflection node count differs')
    require([r['metadata'] for r in roots] == [r['address'] for r in expected],
            'reflection roots differ from inventory')
    images = [r for r in rows if 'image' in r]
    identities = Identities(refs, identity, images)
    instance_fields = {}
    for root in roots:
        if root.get('instance_kind') in (17, 18):
            instance_fields.setdefault(root['type'], set()).update(
                f['type'] for f in root.get('instance_fields', []) if f['type'])
    blockers = Counter()
    for node in nodes.values():
        name, reasons = identities.name(node['name'])
        node['normalized_name'] = name
        node['identity'] = (hashlib.sha256((identity['cache_uuid']+'\0'+name).encode()).hexdigest()
                            if name is not None else None)
        node['kind_name'] = KINDS[node['kind']] if node['kind'] < len(KINDS) else 'unknown'
        if node['kind_name'] == 'unknown':
            reasons.append('layout_unavailable')
        if node['kind_name'] == 'thick_function':
            reasons.append('function_signature_not_exposed')
        if node['kind_name'] == 'strong_reference' and node['node'] not in instance_fields:
            reasons.append('class_instance_not_observed')
        node['unresolved'] = reasons
        blockers.update(reasons)
        node['dependencies'] = sorted(set(node['arguments'] +
                                           [f['type'] for f in node['fields'] if f['type']]) |
                                      instance_fields.get(node['node'], set()))
        require(all(key in nodes for key in node['dependencies']), 'reflection dependency missing')
    for root, observed in zip(roots, expected):
        root['observed_name'] = observed['name']
        root['descriptor_reference'] = identities.reference(root['descriptor'])
        root['reflection_status'] = 'resolved' if root['type'] in nodes else 'unresolved'
        root['instance_dependencies'] = sorted({f['type'] for f in root.get('instance_fields', []) if f['type']})
        require(all(key in nodes for key in root['instance_dependencies']), 'class instance dependency missing')
        # Distinct canonical metadata variants remain separate roots even if their
        # upstream TypeRef/name is the same. Never turn name equality into a hit.
        root['generation_status'] = 'not_tested'
        root['adoption_status'] = 'not_tested'
    conformances = [dict(r, protocol_reference=identities.reference(r['protocol']),
                        metadata_reference=identities.reference(r['metadata']))
                    for r in rows if 'conformance' in r]
    errors = [r['conformance_error'] for r in rows if 'conformance_error' in r]
    summary = dict(inventory_roots=len(roots), reflected_roots=sum(r['type'] in nodes for r in roots),
                   unresolved_roots=sum(r['type'] not in nodes for r in roots), nodes=len(nodes),
                   kinds=dict(Counter(n['kind_name'] for n in nodes.values())),
                   unresolved_nodes=dict(blockers), conformance_observations=len(conformances),
                   unresolved_conformance_types=sum(r['type'] not in nodes for r in conformances),
                   conformance_errors=errors)
    return dict(schema=2, engine='SwiftRemoteMirror', identity=identity, images=images,
                roots=roots, nodes=nodes, conformances=conformances, summary=summary,
                limits=['Observed inventory only; not exhaustive UI-state coverage.',
                        'TypeRef fields describe values, not relocatable metadata payloads.',
                        'Conformance enumeration supplies type/protocol pairs, not witness payloads.',
                        'Function signatures and unresolved layouts remain explicit gaps.',
                        'Generation, installation and canonical runtime adoption are untested here.'])


def main(argv):
    if argv and argv[0] == 'prepare':
        ap = argparse.ArgumentParser(description='Build the signed upstream reflection adapter')
        ap.add_argument('output', type=Path)
        ap.add_argument('--arch', choices=['x86_64', 'arm64'], default='x86_64')
        a = ap.parse_args(argv[1:]); prepare(a.output, a.arch); return 0
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('cache', type=Path); ap.add_argument('inventory', type=Path); ap.add_argument('output', type=Path)
    transport = ap.add_mutually_exclusive_group(required=True)
    transport.add_argument('--reader-command', type=Path, help='JSON argv: SSH, signed reader and process PID')
    transport.add_argument('--captured', type=Path, help='reanalyze saved reflection.jsonl offline')
    a = ap.parse_args(argv)
    inventory = json.loads(a.inventory.read_text())
    a.output.mkdir(parents=True, exist_ok=False)
    captured = a.output/'reflection.jsonl'
    if a.captured:
        shutil.copyfile(a.captured, captured)
    if a.reader_command:
        addresses = ''.join(f"{r['address']:x}\n" for block in inventory for r in block.get('metadata', []))
        with captured.open('w') as out, (a.output/'reflection.log').open('w') as err:
            subprocess.run(json.loads(a.reader_command.read_text()), input=addresses, text=True,
                           stdout=out, stderr=err, check=True)
    rows = [json.loads(line) for line in captured.read_text().splitlines()]
    from .loader_generation import LoaderFamily
    family = LoaderFamily(a.cache)
    try:
        refs = References(family)
        refs.images = {path for path, _ in family.images}
        graph = normalize(rows, inventory, refs)
        require(graph['identity']['cache_uuid'] == str(uuid.UUID(bytes=bytes(family.main.data[88:104]))),
                'live process cache differs')
        write_json(a.output/'graph.json', graph)
        write_json(a.output/'build.json', provenance(sources()+[a.inventory, captured]+
                   ([a.reader_command] if a.reader_command else [])+[f.path for f in family.files],
                   operation='swift-remote-mirror-dependencies', cache_uuid=graph['identity']['cache_uuid']))
        print(json.dumps(graph['summary'], indent=2))
    finally:
        family.close()
    return 0
