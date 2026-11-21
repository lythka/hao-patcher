"""Compare native and v1.4/v1.5 debug witness-map lookup without DYLD overrides."""
import argparse
import json
from pathlib import Path
import subprocess
import uuid
from .exports import require

from .runtime_probes import LoaderFamily, Disassembler, cached_symbols, unique_symbol
from .swift_metadata import pointer_entries, provenance, write_json


def profile(cache):
    family,dis=LoaderFamily(cache),Disassembler()
    try:
        table,roots=pointer_entries(family)
        swift=cached_symbols(family,'/usr/lib/swift/libswiftCore.dylib')
        dyld=cached_symbols(family,'/usr/lib/system/libdyld.dylib')
        _,callback=unique_symbol(swift,'28libPrespecializedImageLoadedEv')
        end=min(a for a in swift.values() if a>callback)
        stores=[i for i in dis.interval(bytes(family.read(callback,end-callback)),callback)[0]
                if i.data[:2]==b'\xc7\x05' and i.data[-4:]==b'\x04\0\0\0']
        if len(stores)!=1: raise ValueError('cannot locate prespecialization mode store')
        key,value=next((k,v[0]) for k,v in table.items() if len(v)==1)
        return dict(cache_uuid=str(uuid.UUID(bytes=bytes(family.main.data[88:104]))),
                    base=roots['base'],data=roots['swift_pointer']-24,table=roots['table'],
                    mode=stores[0].rip_target,key=list(key),value=value,
                    witness=swift['_swift_getWitnessTable'],lookup=dyld['__dyld_find_pointer_hash_table_entry'])
    finally:
        dis.close();family.close()


def script(p):
    return f'''#pragma D option quiet
#pragma D option bufsize=4m
pid$target::hao_witness_phase:entry {{ phase=arg0; }}
pid$target::-:CACHEADDR_{p['witness']:x}_END /phase==1/
{{ printf("W\\t%llx\\t%llx\\n",(uint64_t)uregs[R_RDI],(uint64_t)uregs[R_RSI]); }}
pid$target::-:CACHEADDR_{p['lookup']:x}_END /phase==1 || phase==2/
{{ printf("L\\t%d\\t%llx\\t%llx\\n",phase,(uint64_t)uregs[R_RDI],(uint64_t)uregs[R_RSI]); }}
END {{ printf("WE\\n"); }}
'''


def summarize(text,stderr,p):
    rows=[json.loads(s) for s in text.splitlines() if s.startswith('{')]
    start=next((r for r in rows if r.get('start')),{});end=next((r for r in rows if r.get('complete')), {})
    calls=[s.split('\t') for s in text.splitlines() if s.startswith('L\t')]
    witnesses=[s.split('\t') for s in text.splitlines() if s.startswith('W\t')]
    witness_calls=len(witnesses)
    conformances={r[1] for r in witnesses}
    controls=sum(r[1]=='2' for r in calls);automatic=sum(r[1]=='1' for r in calls)
    witness_lookups=sum(r[1]=='1' and r[3] in conformances for r in calls)
    errors=[s for s in stderr.splitlines() if any(k in s.lower() for k in ('drop','error','invalid','failed'))]
    complete=bool(start and end and start.get('cache_uuid')==p['cache_uuid'] and
                  start.get('override') is False and end.get('override') is False and
                  start.get('mode')==end.get('mode') and start.get('mode') in (2,3) and
                  witness_calls>=8 and controls==2 and end.get('requests')==8 and
                  end.get('positive_hits')==2 and 'WE' in text.splitlines() and not errors)
    return dict(complete=complete,minor=start.get('minor'),mode=start.get('mode'),
                override=start.get('override'),witness_calls=witness_calls,
                automatic_table_calls=automatic,witness_table_calls=witness_lookups,
                positive_table_calls=controls,errors=errors,
                scope='Isolated witness-map capability; debug-library runs are not production adoption.')


def main(argv):
    ap=argparse.ArgumentParser(description=__doc__);actions=ap.add_subparsers(dest='action',required=True)
    runtime_tools=actions.add_parser('runtime-toolchain-prepare')
    for name in ('devkit','output'):runtime_tools.add_argument(name,type=Path)
    runtime_profile=actions.add_parser('runtime-profile')
    for name in ('cache','output'):runtime_profile.add_argument(name,type=Path)
    installed=actions.add_parser('graph-installed-prepare',help='verify installed metadata/witness keys without runtime replacements')
    for name in ('cache','catalog','toolchain','devkit','output'):installed.add_argument(name,type=Path)
    installed.add_argument('--llvm',type=Path,required=True)
    graph=actions.add_parser('graph-prepare',help='record witness keys while replaying static recipes')
    for name in ('cache','recipe','catalog','toolchain','devkit','output'):graph.add_argument(name,type=Path)
    graph.add_argument('--llvm',type=Path,required=True)
    allocate=actions.add_parser('graph-materialize',help='stage joint dependency allocations and separate lookup tables')
    for name in ('cache','catalog','output'):allocate.add_argument(name,type=Path)
    allocate.add_argument('--address',type=lambda value:int(value,0),required=True)
    replay=actions.add_parser('graph-replay-prepare',help='build an isolated native consumer of the materialized candidate')
    for name in ('allocation','catalog','toolchain','devkit','output'):replay.add_argument(name,type=Path)
    replay.add_argument('--llvm',type=Path,required=True)
    replay.add_argument('--runtime-profile',type=Path)
    replay.add_argument('--runtime-toolchain',type=Path)
    replay_check=actions.add_parser('graph-replay-summarize')
    for name in ('allocation','capture','output'):replay_check.add_argument(name,type=Path)
    graph_check=actions.add_parser('graph-summarize')
    for name in ('capture','output'):graph_check.add_argument(name,type=Path)
    consumer=actions.add_parser('consumer-prepare',help='build isolated native witness-consumer fixture')
    for name in ('toolchain','devkit','output'):consumer.add_argument(name,type=Path)
    consumer.add_argument('--llvm',type=Path,required=True)
    assess=actions.add_parser('consumer-summarize')
    assess.add_argument('results',type=Path)
    prep=actions.add_parser('prepare');prep.add_argument('cache',type=Path);prep.add_argument('output',type=Path)
    check=actions.add_parser('summarize')
    for name in ('profile','trace','stderr','output'):check.add_argument(name,type=Path)
    a=ap.parse_args(argv)
    if a.action=='runtime-toolchain-prepare':
        from .witness_runtime import prepare as runtime_prepare
        print(runtime_prepare(a.devkit,a.output));return 0
    if a.action=='runtime-profile':
        from .witness_runtime import profile as runtime_profile
        family=LoaderFamily(a.cache)
        try:
            result=runtime_profile(family);a.output.mkdir(parents=True,exist_ok=False)
            write_json(a.output/'profile.json',result)
            write_json(a.output/'build.json',provenance([Path(__file__).with_name('witness_runtime.py')]+[f.path for f in family.files],
                operation='swift-witness-runtime-profile',cache_uuid=result['cache_uuid']))
            print(json.dumps(result,indent=2))
        finally:family.close()
        return 0
    if a.action=='graph-replay-summarize':
        from .witness_graph import replay_summary
        result=replay_summary(json.loads(a.allocation.read_text()),
                              [json.loads(line) for line in a.capture.read_text().splitlines()])
        write_json(a.output,result);print(json.dumps(result,indent=2));return 0 if result['complete'] else 1
    if a.action=='graph-installed-prepare':
        from .witness_graph import replay_prepare
        print(replay_prepare(None,a.catalog,a.toolchain,a.devkit,a.output,a.llvm,installed_cache=a.cache));return 0
    if a.action=='graph-replay-prepare':
        from .witness_graph import replay_prepare
        print(replay_prepare(a.allocation,a.catalog,a.toolchain,a.devkit,a.output,a.llvm,
                            a.runtime_profile,a.runtime_toolchain));return 0
    if a.action=='graph-materialize':
        from .witness_graph import materialize
        print(materialize(a.cache,a.catalog,a.output,a.address));return 0
    if a.action=='graph-prepare':
        from .witness_graph import prepare
        print(prepare(a.cache,a.recipe,a.catalog,a.toolchain,a.devkit,a.output,a.llvm));return 0
    if a.action=='graph-summarize':
        from .witness_graph import summarize as summarize_graph
        result=summarize_graph([json.loads(line) for line in a.capture.read_text().splitlines()])
        write_json(a.output,result);print(json.dumps(result['summary'],indent=2));return 0 if result['complete'] else 1
    if a.action=='consumer-prepare':
        from .witness_consumer import prepare
        print(prepare(a.toolchain,a.devkit,a.output,a.llvm));return 0
    if a.action=='consumer-summarize':
        from .witness_consumer import summarize as summarize_consumer
        result=summarize_consumer(a.results)
        write_json(a.results/'summary.json',result);print(json.dumps(result,indent=2))
        return 0 if result['consumer_supported'] else 1
    if a.action=='summarize':
        r=summarize(a.trace.read_text(),a.stderr.read_text(),json.loads(a.profile.read_text()))
        write_json(a.output,r);print(json.dumps(r));return 0 if r['complete'] else 1
    p=profile(a.cache);a.output.mkdir(parents=True)
    write_json(a.output/'profile.json',p);(a.output/'witness.d').write_text(script(p))
    import struct
    require(0 < len(p['key']) <= 64, 'witness profile key exceeds helper capacity')
    (a.output/'witness-profile.bin').write_bytes(struct.pack('<40s69Q',
        p['cache_uuid'].encode(), p['table']-p['base'], p['data']-p['base'], p['mode']-p['base'],
        p['value'], len(p['key']), *p['key'], *([0]*(64-len(p['key'])))))
    from .helper_build import probe
    root=Path(__file__).resolve().parents[2];source=root/'tools/swift-witness-probe.c'
    for name, filename in (('witness-probe', 'witness-probe'), ('witness-v4', 'v4.dylib'), ('witness-v5', 'v5.dylib')):
        probe(name, a.output/filename)
    probe('bind-runtime-probes', a.output/'bind-runtime-probes')
    runner=a.output/'run.sh'
    runner.write_bytes((root/'tools/swift-witness-run.sh').read_bytes());runner.chmod(0o755)
    family=LoaderFamily(a.cache)
    try:write_json(a.output/'build.json',provenance([f.path for f in family.files]+[
        source,Path(__file__),root/'tools/swift-witness-run.sh',root/'tools/bind-runtime-probes.c'],operation='witness-map-probe'))
    finally:family.close()
    print(a.output)
    return 0
