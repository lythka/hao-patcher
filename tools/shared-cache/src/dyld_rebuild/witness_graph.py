"""Replay static recipes while recording their actual witness constructor keys."""
from collections import Counter
import json
import os
from pathlib import Path
import struct
import subprocess
import uuid

from .swift_metadata import ROOT, capture, provenance, write_json
from .witness_consumer import native_flags, GUM_VERSION
from .swift_abi import schemas
from .host_tools import builder_objects


def prepare(cache, recipe, catalog, toolchain, devkit, output, llvm):
    from .artifacts import helper
    pinned = helper('witness-graph')
    if pinned is None:
        return produce(cache, recipe, catalog, toolchain, devkit, output, llvm)
    import shutil
    capture(cache, recipe, output, prepare_only=True, base_catalog=catalog)
    shutil.copy2(output / 'build.json', output / 'capture-build.json')
    shutil.copy2(pinned, output / 'swift-capture')
    write_json(output / 'build.json', provenance([pinned, cache, recipe, catalog],
               operation='witness-constructor-graph', architecture='x86_64'))
    return output


def produce(cache, recipe, catalog, toolchain, devkit, output, llvm):
    output = output.resolve()
    capture(cache, recipe, output, prepare_only=True, base_catalog=catalog)
    (output/'capture-build.json').write_bytes((output/'build.json').read_bytes())
    commands = compile_fixture(toolchain, devkit, output, llvm)
    c_source = ROOT/'tools/swift-graph.c'
    cpp_source = ROOT/'tools/swift-witness-graph.cpp'
    inputs = [cache, recipe, catalog, toolchain/'build.json', devkit, output/'capture-build.json',
              c_source, cpp_source, ROOT/'tools/cache-address.h', ROOT/'tools/witness-runtime.h',
              Path(__file__), Path(__file__).with_name('witness_consumer.py')]
    write_json(output/'build.json', provenance(inputs, operation='witness-constructor-graph',
               architecture='x86_64', frida_gum=GUM_VERSION, commands=commands))
    return output


def compile_fixture(toolchain, devkit, output, llvm):
    flags = native_flags(toolchain, devkit, output, llvm)
    c_source = ROOT/'tools/swift-graph.c'
    cpp_source = ROOT/'tools/swift-witness-graph.cpp'
    object_file = output/'collector.o'
    commands = [
        ['xcrun', 'clang', '-arch', 'x86_64', '-mmacosx-version-min=14.0', '-O2',
         '-Wall', '-Wextra', '-Werror', '-DSWIFT_WITNESS_GRAPH', '-c', str(c_source), '-o', str(object_file)],
        flags + [str(cpp_source), str(object_file), '-L', str(output/'gum'), '-lfrida-gum',
                 '-lresolv', '-o', str(output/'swift-capture')],
        ['codesign', '-s', '-', str(output/'swift-capture')],
    ]
    with (output/'compile.log').open('w') as log:
        for command in commands:
            subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=True)
    return commands


def materialize(cache, catalog_path, output, address):
    """Stage a jointly relocated candidate, without changing an image or runtime."""
    from .exports import require
    from .loader_generation import LoaderFamily
    from .swift_graph import materialize_dependencies
    from .swift_metadata import emit_table
    catalog = json.loads(catalog_path.read_text())
    family = LoaderFamily(cache)
    try:
        from .witness_initializers import normalize
        catalog,initializers=normalize(catalog,family)
        cache_uuid = str(uuid.UUID(bytes=bytes(family.main.data[88:104])))
        require(catalog['source_cache_uuid']==cache_uuid, 'Swift dependency source cache UUID differs')
        allocation = materialize_dependencies(catalog, family, address)
        output.mkdir(parents=True,exist_ok=False)
        write_json(output/'witness-initializers.json',initializers)
        (output/'payload.bin').write_bytes(allocation['data'])
        maps = {}
        for name in ('metadata','witness') + (('metatype',) if allocation['metatype_entries'] else ()):
            entries = allocation[name+'_entries']
            _, lookup = emit_table(entries, output/name)
            maps[name] = dict(entries=[dict(key=list(k),values=v) for k,v in sorted(entries.items())],lookup=lookup)
        layout = dict(schema=1,kind='swift-dependency-allocation',cache_uuid=cache_uuid,
                      address=address,size=len(allocation['data']),roots=allocation['roots'],
                      pointers=allocation['pointers'],records=allocation['records'],maps=maps,
                      scope='Relocated payload and separately verified lookup tables; not an installed image.')
        write_json(output/'allocation.json',layout)
        from .artifacts import helper
        binary = helper('swift-table-writer')
        toolchain_inputs = [binary] if binary is not None else []
        write_json(output/'build.json',provenance([catalog_path,Path(__file__),
            Path(__file__).with_name('swift_graph.py'),Path(__file__).with_name('swift_metadata.py'),
            Path(__file__).with_name('witness_initializers.py'),
            Path(__file__).with_name('host_tools.py'),ROOT/'tools/swift-table-writer.cpp']+
            [f.path for f in family.files]+toolchain_inputs,
            operation='swift-dependency-allocation',cache_uuid=cache_uuid,address=address,
            metadata_records=len(catalog['records']),witness_records=len(catalog['witnesses'])))
        return output
    finally:
        family.close()


def replay_prepare(allocation_path, catalog_path, toolchain, devkit, output, llvm,
                   runtime_profile=None,runtime_toolchain=None,installed_cache=None):
    """Build exact-key verification against isolated data or an installed cache."""
    catalog=json.loads(catalog_path.read_text())
    installed=installed_cache is not None
    if installed:
        from .swift_installed import records as installed_records
        from .loader_generation import LoaderFamily
        from .exports import require
        require(runtime_profile is None and runtime_toolchain is None,
                'installed verification does not replace runtime behavior')
        family=LoaderFamily(installed_cache)
        try:
            rows,roots=installed_records(catalog,family)
            layout=dict(schema=1,installed=True,cache_uuid=str(uuid.UUID(bytes=bytes(family.main.data[88:104]))),
                        roots=roots,address=0,pointers=[],records=rows)
            allocation_inputs=[f.path for f in family.files]+[Path(__file__).with_name('swift_installed.py')]
        finally:family.close()
        payload=b''
    else:
        layout=json.loads((allocation_path/'allocation.json').read_text())
        payload=(allocation_path/'payload.bin').read_bytes()
        allocation_inputs=[allocation_path/name for name in ('build.json','allocation.json','payload.bin')]
    output=output.resolve();output.mkdir(parents=True,exist_ok=False)
    write_json(output/'allocation.json',layout)
    raw=bytearray((b'SWINS001' if installed else b'SWDEP001')+uuid.UUID(layout['cache_uuid']).bytes)
    raw+=struct.pack('<5Q',layout['roots']['base'],layout['address'],len(payload),
                     len(layout['pointers']),len(layout['records']))+payload
    for p in layout['pointers']: raw+=struct.pack('<QQ',p['address'],p['target'])
    for row in layout['records']:
        raw+=struct.pack('<3Q',(2 if row.get('abi_family')=='metatype' else int(row['kind']=='protocol-witness')),len(row['key']),row['address'])
        raw+=struct.pack('<'+str(len(row['key']))+'Q',*[layout['roots']['base']+k for k in row['key']])
    (output/'candidate.bin').write_bytes(raw)
    # Discovery's loads include owners of unresolved recipes and foreign-platform
    # images. Replay uses exact generated keys, so load only this payload's owners.
    loads=set()
    from .swift_graph import optional_records
    source_records=catalog['records'] if catalog.get('kind')=='swift-dependency-catalog' else optional_records(catalog)
    for row in source_records+catalog.get('witnesses',[]):
        loads.update(ref['image'] for ref in row['key']+[p['ref'] for p in row['pointers']] if 'image' in ref)
    (output/'loads.txt').write_text(''.join(path+'\n' for path in sorted(loads)))
    source=ROOT/'tools/swift-dependency-replay.cpp'
    from .artifacts import helper
    from .exports import require
    name = 'dependency-installed' if installed else 'dependency-replay'
    runtime_inputs=[]
    if runtime_profile is not None:
        profile=json.loads(runtime_profile.read_text())
        require(profile['cache_uuid']==layout['cache_uuid'],'witness runtime profile belongs to another cache')
        require(runtime_toolchain is not None,'runtime replay needs its source-built toolchain')
        pinned_object = helper('runtime-object')
        if pinned_object is not None:
            require((runtime_toolchain/'runtime.o').read_bytes() == pinned_object.read_bytes(),
                    'runtime replay object differs from pinned runtime')
        (output/'runtime-profile.bin').write_bytes(struct.pack('<3Q',
            *(profile[field]-profile['base'] for field in ('once','initialize','mode'))))
        runtime_inputs=[runtime_profile,runtime_toolchain/'runtime.o',ROOT/'tools/swift-witness-runtime.h']
        name = 'dependency-runtime'
    binary = helper(name, output/'dependency-replay')
    if binary is None:
        compile_replay(toolchain, devkit, output/'developer', llvm, name,
                       runtime_toolchain/'runtime.o' if runtime_toolchain else None)
        import shutil
        shutil.copy2(output/'developer/dependency-replay', output/'dependency-replay')
    commands=[]
    write_json(output/'build.json',provenance(allocation_inputs+[catalog_path,output/'dependency-replay',source,ROOT/'tools/witness-runtime.h',
        Path(__file__),Path(__file__).with_name('witness_consumer.py')]+runtime_inputs,operation='swift-installed-verification' if installed else 'swift-dependency-replay',
        commands=commands,architecture='x86_64',frida_gum=GUM_VERSION,cache_uuid=layout['cache_uuid'],
        scope=('Installed exact-key identity verification without runtime replacements; application adoption remains open.' if installed else
               'Isolated native relocated-record replay; no installed dyld table or app adoption is proven.')))
    return output


def compile_replay(toolchain, devkit, output, llvm, name, runtime_object=None):
    output.mkdir(parents=True, exist_ok=False)
    flags=native_flags(toolchain,devkit,output,llvm)
    if name == 'dependency-installed':
        flags += ['-DSWIFT_DEPENDENCY_INSTALLED']
    if name == 'dependency-runtime':
        flags += ['-DSWIFT_WITNESS_RUNTIME', str(runtime_object)]
    binary = output/'dependency-replay'
    subprocess.run(flags+[str(ROOT/'tools/swift-dependency-replay.cpp'),'-L',str(output/'gum'),
                         '-lfrida-gum','-lresolv','-o',str(binary)],check=True)
    from .helper_sign import sign
    sign(binary, 'org.hao.'+name)
    return binary


def replay_summary(layout, events):
    """Verify every identity; report native-selected cache misses separately."""
    expected={i:r['kind'] for i,r in enumerate(layout['records'])}
    results=[r for r in events if 'record' in r]
    indices=[r['record'] for r in results]
    footer=events[-1] if events else {}
    counts=Counter(expected.values())
    witnesses=[r for r in results if expected.get(r['record'])=='protocol-witness']
    modern='ordinary_validated' in footer
    def ordinary_valid(row):
        if not modern:
            return 'ordinary_valid' not in row and row.get('ordinary') is True
        if row.get('ordinary_valid') is not True:
            return False
        if row.get('ordinary_source')=='catalog':
            return row.get('ordinary') is True and expected.get(row.get('ordinary_record'))=='protocol-witness'
        return (row.get('ordinary_source')=='runtime' and row.get('ordinary') is False and
                row.get('ordinary_record') is None)
    failures=[r['record'] for r in results if r.get('direct') is not True or
              (expected.get(r['record'])=='protocol-witness' and not ordinary_valid(r))]
    adopted=[r['record'] for r in witnesses if r.get('ordinary') is True]
    uncovered=[r['record'] for r in witnesses if r.get('ordinary_source')=='runtime']
    variants=[r['record'] for r in witnesses if r.get('ordinary_source')=='catalog' and
              expected.get(r.get('ordinary_record'))=='protocol-witness' and
              layout['records'][r['ordinary_record']].get('key')!=layout['records'][r['record']].get('key')]
    ordinary_counts=(footer.get('ordinary_adopted')==len(adopted) and
                     len(adopted)+len(uncovered)==counts['protocol-witness'])
    if modern:
        ordinary_counts=ordinary_counts and (
            footer.get('ordinary_validated')==counts['protocol-witness'] and
            footer.get('ordinary_fallbacks')==len(uncovered) and
            footer.get('ordinary_variants')==len(variants))
    complete=bool(footer.get('complete') and sorted(indices)==list(expected) and not failures and
        footer.get('metadata_requests')==footer.get('metadata_names')==counts['metadata'] and
        footer.get('witness_requests')==footer.get('ordinary_requests')==counts['protocol-witness'] and
        ordinary_counts and footer.get('failures')==0 and
        bool(footer.get('installed'))==bool(layout.get('installed')) and
        (not footer.get('runtime_policy') or footer.get('policy_disabled_fallback') is True))
    metatypes=sum(r.get('abi_family')=='metatype' for r in layout['records'])
    metatype_events=[r for r in events if r.get('metatype_summary')]
    if metatypes:
        complete=complete and len(metatype_events)==1 and all(
            metatype_events[0].get(field)==metatypes for field in ('requests','resolved','value_operations'))
    associated=[r for r in events if r.get('associated_summary')]
    if associated:
        complete=complete and len(associated)==1 and associated[0].get('before_metadata_replay') is True and (
            associated[0].get('requests')==associated[0].get('validated'))
    return dict(complete=bool(complete),counts=dict(counts),footer=footer,failed_records=failures,
        ordinary_coverage=dict(requests=counts['protocol-witness'],adopted=len(adopted),
            uncovered_records=uncovered,alternate_records=variants,
            complete=bool(complete and not uncovered)),
        metatypes=metatype_events[0] if metatype_events else None,
        associated_coverage=associated[0] if associated else None,
        scope=('Installed cache identity verification without replacements. ' if layout.get('installed') else
               'Own-process native identity verification; installed dyld table lookup is untested. ')+
              'Ordinary lookup validates the native-selected constructor; its catalog adoption and runtime misses '
              'are reported separately. Arbitrary value operations and application adoption remain open.')



def summarize(captured):
    rows=schemas(captured)
    by_pointer={}
    for row in rows:by_pointer.setdefault(row['protocol_witness'],[]).append(row)
    blocked=[r for r in captured if r.get('reason')=='protocol witness dependency is outside shared cache']
    endings=[r for r in captured if r.get('witness_graph')]
    complete=bool(captured and captured[-1].get('complete') and len(endings)==1 and endings[0]['records']==len(rows))
    requested={p for row in blocked for p in row.get('key',[])[1+row.get('type_arguments',0):]}
    return dict(schema=1, complete=complete, records=rows, blocked_nodes=blocked,
                summary=dict(witness_records=len(rows), unique_witnesses=len(by_pointer),
                  schema_supported=sum(not r['errors'] for r in rows),
                  schema_errors=dict(Counter(e for r in rows for e in r['errors'])),
                  blocked_metadata_nodes=len(blocked), requested_witnesses=len(requested),
                  observed_requested_witnesses=len(requested & by_pointer.keys()),
                  recorder=endings[0] if endings else None),
                scope='Runtime constructor keys and ABI-typed witness slots. This is capture data; '
                      'cache relocation, metadata dependency closure and installed adoption remain separate.')


def merge_catalogs(catalogs):
    from .exports import require
    from .swift_graph import record_id
    metadata,witnesses,loads,counts={},{},set(),[]
    cache_uuid=catalogs[0]['source_cache_uuid']
    for catalog in catalogs:
        require(catalog.get('schema')==4 and catalog.get('kind')=='swift-dependency-catalog',
                'expected a generated dependency catalog')
        require(catalog['source_cache_uuid']==cache_uuid,'dependency catalogs target different caches')
        before=(len(metadata),len(witnesses))
        for field,table in (('records',metadata),('witnesses',witnesses)):
            for row in catalog[field]:
                identity=record_id(row)
                if identity in table:
                    # Anonymous printable names include process addresses. The
                    # exact key, bytes and relocations determine record identity.
                    semantic=lambda r:{k:v for k,v in r.items() if k!='name'}
                    require(semantic(table[identity])==semantic(row),'conflicting generated dependency '+identity)
                else:table[identity]=row
        loads.update(catalog.get('loads',[]))
        counts.append(dict(metadata_added=len(metadata)-before[0],witnesses_added=len(witnesses)-before[1]))
    all_ids=metadata.keys()|witnesses.keys()
    for row in list(metadata.values())+list(witnesses.values()):
        require(all(ref['owned'] in all_ids for ref in row['key']+[p['ref'] for p in row['pointers']] if 'owned' in ref),
                'generated catalog has a missing owned dependency')
    return dict(schema=4,kind='swift-dependency-catalog',source_cache_uuid=cache_uuid,
                loads=sorted(loads),records=list(metadata.values()),witnesses=list(witnesses.values())),counts
