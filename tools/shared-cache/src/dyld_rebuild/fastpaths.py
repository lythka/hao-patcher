"""Measure ordinary Swift conformance table use without forcing runtime gates."""
import argparse
import json
from pathlib import Path
import subprocess
import uuid

from .runtime_probes import (LoaderFamily, FamilyReader, Disassembler, cached_symbols,
                             unique_symbol, table_calls, sha)


def profile(cache):
    family, dis = LoaderFamily(cache), Disassembler()
    try:
        swift = cached_symbols(family, '/usr/lib/swift/libswiftCore.dylib')
        dyld = cached_symbols(family, '/usr/lib/system/libdyld.dylib')
        _, start = unique_symbol(swift, '__ZL52swift_conformsToProtocolMaybeInstantiateSuperclasses')
        end = min(a for a in swift.values() if a > start)
        calls = table_calls(dis, family.read, FamilyReader(family).ptr, start, end-start,
                            dyld['__dyld_find_protocol_conformance'])
        return dict(cache_uuid=str(uuid.UUID(bytes=bytes(family.main.data[88:104]))),
                    calls=[dict(call=start+r['call'], resume=start+r['resume']) for r in calls],
                    inputs=[dict(path=str(f.path.resolve()), sha256=sha(f.path)) for f in family.files])
    finally:
        dis.close()
        family.close()


def generate(p):
    # The exported libdyld function tail-jumps, so DTrace has no return probe.
    # Observe Swift immediately before and after its ordinary call instead.
    lines = ['#pragma D option quiet']
    for row in p['calls']:
        lines.append(f'''pid$target::-:CACHEADDR_{row['call']:x}_END
{{
 self->cf_seq++; self->cf_sp=uregs[R_RSP];
 printf("CQ\\t%llu\\t%llu\\t%llx\\t%llx\\t%llx\\n",
 (uint64_t)tid,(uint64_t)self->cf_seq,(uint64_t)uregs[R_RDI],
 (uint64_t)uregs[R_RSI],(uint64_t)uregs[R_RDX]);
}}
pid$target::-:CACHEADDR_{row['resume']:x}_END
/self->cf_sp == uregs[R_RSP]/
{{
 printf("CR\\t%llu\\t%llu\\t%llu\\t%llx\\n",
 (uint64_t)tid,(uint64_t)self->cf_seq,(uint64_t)uregs[R_RAX],(uint64_t)uregs[R_RDX]);
 self->cf_sp=0;
}}''')
    lines.append('END { printf("CE\\n"); }')
    return '\n'.join(lines)+'\n'


def summarize(text, p, stderr=''):
    requests, returns, helper = {}, {}, []
    duplicate, ended, malformed = False, False, False
    for line in text.splitlines():
        if line.startswith('{'):
            helper.append(json.loads(line))
            continue
        fields = line.split('\t')
        try:
            if fields[0] == 'CQ':
                key = tuple(map(int, fields[1:3]))
                duplicate |= key in requests
                requests[key] = dict(protocol=int(fields[3],16), metadata=int(fields[4],16),
                                     descriptor=int(fields[5],16))
            elif fields[0] == 'CR':
                key = tuple(map(int, fields[1:3]))
                duplicate |= key in returns
                returns[key] = dict(kind=int(fields[3]), value=int(fields[4],16))
            elif fields[0] == 'CE':
                ended = True
        except (ValueError, IndexError):
            malformed = True
    identity = next((r for r in helper if 'cache_uuid' in r), {})
    pairs = [dict(**r, **returns[k]) for k,r in requests.items() if k in returns]
    counts = {name:sum(r['kind']==kind for r in pairs) for kind,name in enumerate(
        ('descriptor_hits','witness_hits','not_found','definitive_failure'))}
    counts['unknown_result'] = sum(r['kind'] not in range(4) for r in pairs)
    counts['incomplete'] = len(set(requests)^set(returns))
    expected = []
    for row in helper:
        if 'witness' not in row:
            continue
        matched = [r for r in pairs if r['protocol']==row['protocol_descriptor'] and
                   ((r['metadata'] and r['metadata']==row['metadata']) or
                    (r['descriptor'] and r['descriptor']==row['type_descriptor']))]
        expected.append(dict(type=row['type'],protocol=row['protocol'],
            runtime_witness=bool(row['witness']),table_hits=sum(r['kind'] in (0,1) and bool(r['value']) for r in matched),
            table_misses=sum(r['kind'] in (2,3) for r in matched)))
    clean = not any(word in stderr.lower() for word in ('drop','invalid','error','failed'))
    complete = (identity.get('cache_uuid')==p['cache_uuid'] and identity.get('mode')=='runtime'
                and ended and clean and not duplicate and not malformed and not counts['incomplete']
                and bool(expected) and helper[-1:]==[dict(complete=True,failures=0)])
    return dict(complete=complete,cache_uuid=identity.get('cache_uuid'),override=identity.get('override'),
                counts=counts,queries=expected,
                all_queries_table_hits=complete and all(r['runtime_witness'] and r['table_hits']>0 for r in expected),
                scope='Ordinary protocol queries in this helper. App workloads and launch timing are separate.')


def main(argv):
    ap=argparse.ArgumentParser(description=__doc__)
    actions=ap.add_subparsers(dest='action',required=True)
    prep=actions.add_parser('prepare')
    prep.add_argument('cache',type=Path)
    prep.add_argument('output',type=Path)
    report=actions.add_parser('summarize')
    report.add_argument('profile',type=Path)
    report.add_argument('trace',type=Path)
    report.add_argument('stderr',type=Path)
    report.add_argument('output',type=Path)
    a=ap.parse_args(argv)
    if a.action=='summarize':
        result=summarize(a.trace.read_text(),json.loads(a.profile.read_text()),a.stderr.read_text())
        a.output.write_text(json.dumps(result,indent=2)+'\n')
        print(json.dumps(result))
        return 0 if result['complete'] else 1
    p=profile(a.cache)
    a.output.mkdir(parents=True)
    (a.output/'profile.json').write_text(json.dumps(p,indent=2)+'\n')
    (a.output/'conformance.d').write_text(generate(p))
    root=Path(__file__).resolve().parents[2]
    for name in ('swift-conformance-probe','bind-runtime-probes'):
        source=root/'tools'/(name+'.c')
        binary=a.output/name
        from .helper_build import probe
        probe(name, binary)
    from .swift_metadata import provenance, write_json
    inputs=[Path(row['path']) for row in p['inputs']]+[Path(__file__)]+[
        root/'tools'/(name+'.c') for name in ('swift-conformance-probe','bind-runtime-probes')]
    write_json(a.output/'build.json',provenance(inputs,operation='fastpaths',cache_uuid=p['cache_uuid']))
    print(a.output)
    return 0
