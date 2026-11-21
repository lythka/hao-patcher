"""Turn upstream reflected generic arguments into a portable constructor graph.

The report accounts for every observed root. Graph nodes describe constructors and
symbolic leaf names, never copied heap metadata or old process addresses.
"""
import argparse
from collections import Counter
import json
from pathlib import Path
import re
import struct
import subprocess
import uuid

from .exports import Refusal, require
from .swift_metadata import References, provenance, write_json
from .swift_graph import is_optional
from .metadata_graph import MetadataGraph


def identify_inputs(portable, reflected, binaries):
    """Associate captured image UUIDs with the actual analyzed binary bytes."""
    from .metadata_expand import binary_inputs
    observed = {image['uuid'].lower() for image in reflected['images']}
    for binary in binaries:
        from .producer import x86_64_uuid
        identity = x86_64_uuid(binary)
        require(identity is not None and str(uuid.UUID(hex=identity)).lower() in observed,
                'reflection input binary UUID was not captured; renew reflection')
    portable['inputs'] = binary_inputs(binaries)


def plan(family, graph, base_catalog, optionals_only=False):
    cache_uuid = str(uuid.UUID(bytes=bytes(family.main.data[88:104])))
    require(graph['engine'] == 'SwiftRemoteMirror' and graph['identity']['cache_uuid'] == cache_uuid,
            'reflection graph and runtime cache differ')
    refs = References(family)
    nodes = {int(k): v for k, v in graph['nodes'].items()}
    by_type = {}
    for root in graph['roots']:
        by_type.setdefault(root['type'], []).append(root)
    # Conformance observations also identify nongeneric leaves absent from the
    # generic inventory. Use a unique file-backed identity instead of trying to
    # resolve a private address-bearing name in a fresh process.
    observed = {}
    for row in graph.get('conformances', []):
        observed.setdefault(row['type'], {})[row['metadata']] = row.get('metadata_reference')
    leaves = {key: next(iter(values.values())) for key, values in observed.items()
              if len(values) == 1 and next(iter(values.values())) and
              'section' in next(iter(values.values()))}
    descriptions = {}
    def describe(root):
        node = nodes.get(root['type'])
        if node is None:
            return dict(category='unresolved_type')
        ref = root.get('descriptor_reference')
        if not ref:
            return dict(category='non_nominal_'+node['kind_name'])
        if 'section' not in ref:
            return dict(category='app_owned_descriptor')
        descriptor = refs.decode(ref)
        flags = struct.unpack('<I', family.read(descriptor, 4))[0]
        if flags & 31 not in (17, 18) or not flags & 128:
            return dict(category='not_generic_value')
        params, requirements, count, context = struct.unpack('<4H', family.read(descriptor+36, 8))
        details = dict(parameters=params, key_arguments=count, reflection_arguments=len(node['arguments']))
        if context & ~2 or count > 63 or params > count:
            return dict(category='extended_generic_context', **details)
        if params != len(node['arguments']) or not params:
            return dict(category='reflection_arguments_incomplete', **details)
        if bytes(family.read(descriptor+44, params)) != b'\x80'*params:
            return dict(category='non_type_generic_parameter', **details)
        # Reflection exposes type arguments, not witness pointers. The native
        # collector resolves key requirements through ordinary conformance
        # lookup, using the descriptor's upstream ABI schema.
        return dict(category='runtime_constructor_candidate', descriptor_reference=ref,
                    owner=ref['image'], witness_arguments=count-params, **details)
    for root in graph['roots']:
        descriptions[root['root']] = describe(root)
    definitions, failures, visiting = {}, {}, set()
    loads = set(base_catalog.get('loads', []))
    def construct(key):
        if key in definitions:
            return str(key)
        if key in failures:
            raise Refusal(failures[key])
        require(key not in visiting, 'recursive_constructor_graph')
        visiting.add(key)
        try:
            node = nodes[key]
            roots = by_type.get(key, [])
            if len(roots) > 1:
                raise Refusal('multiple_metadata_variants_for_typeref')
            if roots:
                info = descriptions[roots[0]['root']]
                require(info['category'] == 'runtime_constructor_candidate', info['category'])
                arguments = [construct(child) for child in node['arguments']]
                definition = dict(id=str(key), operation='generic', descriptor_reference=info['descriptor_reference'], arguments=arguments)
                loads.add(info['owner'])
            elif key in leaves:
                definition = dict(id=str(key), operation='reference', metadata_reference=leaves[key])
                loads.add(leaves[key]['image'])
            else:
                name = node.get('name')
                require(name and not re.search(r'\$[0-9a-fA-F]{6,16}LL', name),
                        'anonymous_dependency_without_descriptor')
                require(node['kind_name'] != 'unknown', 'dependency_layout_unavailable')
                definition = dict(id=str(key), operation='resolve', name=name)
            definitions[key] = definition
            return str(key)
        except (Refusal, KeyError) as error:
            failures[key] = str(error)
            raise Refusal(str(error))
        finally:
            visiting.discard(key)
    entries, records = [], []
    for root in graph['roots']:
        info = descriptions[root['root']]
        selected = not optionals_only or is_optional(root['observed_name'])
        row = dict(root=root['root'], name=root['observed_name'], type=root['type'],
                   selected=selected, **info)
        if selected and info['category'] == 'runtime_constructor_candidate':
            try:
                dependency = construct(root['type'])
                records.append(dict(id='reflection-root-'+str(root['root']), name=root['observed_name'],
                                    owner=info['owner'], dependency=dependency))
                row['generation_plan'] = 'candidate'
            except Refusal as error:
                row.update(generation_plan='blocked', dependency_reason=str(error))
        elif selected:
            row.update(generation_plan='blocked', dependency_reason=info['category'])
        entries.append(row)
    # Construction can leave definitions from a partially blocked root. Include
    # only the transitive closure actually used by selected candidates.
    needed = set()
    def include(key):
        key = int(key)
        if key in needed:return
        for child in definitions[key].get('arguments', []):include(child)
        needed.add(key)
    for record in records:include(record['dependency'])
    report = dict(cache_uuid=cache_uuid, observed=len(entries), selected=sum(r['selected'] for r in entries),
                  candidates=len(records), root_categories=dict(Counter(r['category'] for r in entries)),
                  dependency_blockers=dict(Counter(r['dependency_reason'] for r in entries if 'dependency_reason' in r)),
                  definitions=len(needed), entries=entries,
                  scope='Reflected type constructors, not exact historical pointer keys or successful generation. '
                        'Native capture must establish canonical identity and a serializable closure; installed adoption is separate.')
    portable = MetadataGraph.from_plan([v for k,v in definitions.items() if k in needed], records, loads)
    portable.frontend_evidence(report)
    return portable.document, report


def main(argv):
    ap=argparse.ArgumentParser(description=__doc__)
    for name in ('cache','graph','base_catalog','output'):ap.add_argument(name,type=Path)
    ap.add_argument('--optionals-only',action='store_true')
    ap.add_argument('--binary', type=Path, action='append', required=True,
                    help='captured application binary; repeat for multiple inputs')
    a=ap.parse_args(argv)
    from .loader_generation import LoaderFamily
    family=LoaderFamily(a.cache)
    try:
        reflected=json.loads(a.graph.read_text())
        portable,report=plan(family,reflected,json.loads(a.base_catalog.read_text()),a.optionals_only)
        identify_inputs(portable,reflected,a.binary)
        a.output.mkdir(parents=True,exist_ok=False)
        write_json(a.output/'graph.json',portable);write_json(a.output/'dependencies.json',report)
        write_json(a.output/'build.json',provenance([a.graph,a.base_catalog,*a.binary,Path(__file__),Path(__file__).with_name('metadata_graph.py')]+[f.path for f in family.files],
                   operation='swift-reflection-generation-plan',cache_uuid=report['cache_uuid']))
        print(json.dumps({k:v for k,v in report.items() if k!='entries'},indent=2))
    finally:family.close()
