"""Capture complete generic request keys and canonical return pointers read-only.

Unlike a prespecialized-table miss trace, these probes bracket the actual runtime
constructor. Arguments come from the request, including inherited parameters and
witnesses; no TypeRef-to-key reconstruction is involved.
"""
import argparse
from collections import Counter
import json
from pathlib import Path
import subprocess

from .swift_metadata import provenance, write_json


def generate(profile, seconds=30):
    f=profile['functions']['generic_return']
    entry=f'pid$target::-:CACHEADDR_{profile["generic_address"]:x}_END'
    returns=',\n'.join(f'pid$target::-:CACHEADDR_{f["address"]+offset:x}_END' for offset in f['returns'])
    rows=['#pragma D option quiet','#pragma D option bufsize=32m','#pragma D option dynvarsize=32m',
          f'dtrace:::BEGIN {{ printf("B\\t{profile["cache_uuid"]}\\n"); }}',
          entry+'''\n{
 self->rg_depth++; rg_calls++;
 rg_desc[tid,self->rg_depth]=uregs[R_RDX];
 rg_request[tid,self->rg_depth]=uregs[R_RDI];
 self->rg_flags=*(uint32_t*)copyin(uregs[R_RDX],4);
 self->rg_kind=self->rg_flags&31;
 self->rg_count=0;self->rg_params=0;
 if(self->rg_flags&128) {
  if(self->rg_kind==16) {
   self->rg_count=*(uint16_t*)copyin(uregs[R_RDX]+56,2);
   self->rg_params=*(uint16_t*)copyin(uregs[R_RDX]+52,2);
  }
  if(self->rg_kind==17 || self->rg_kind==18) {
   self->rg_count=*(uint16_t*)copyin(uregs[R_RDX]+40,2);
   self->rg_params=*(uint16_t*)copyin(uregs[R_RDX]+36,2);
  }
 }
 rg_count[tid,self->rg_depth]=self->rg_count;
 rg_params[tid,self->rg_depth]=self->rg_params;
''']
    for i in range(63):
        rows.append(f' if(self->rg_count>{i}) rg_arg[tid,self->rg_depth,{i}]=*(uint64_t*)copyin(uregs[R_RSI]+{8*i},8);')
    rows.append('}')
    rows.append(returns+'''\n/self->rg_depth>0/\n{
 self->rg_result=uregs[R_RAX]; self->rg_state=uregs[R_RDX];
 if(!rg_seen[self->rg_result,self->rg_state]) {
  rg_seen[self->rg_result,self->rg_state]=1;
  rg_sequence[tid]++;
  printf("R\\t%llu\\t%llu\\t%llx\\t%u\\t%u\\t%llx\\t%llu\\t%llu\\n",
    (uint64_t)tid,(uint64_t)rg_sequence[tid],(uint64_t)rg_desc[tid,self->rg_depth],(uint32_t)rg_params[tid,self->rg_depth],
    (uint32_t)rg_count[tid,self->rg_depth],(uint64_t)self->rg_result,(uint64_t)self->rg_state,(uint64_t)rg_request[tid,self->rg_depth]);
''')
    for i in range(63):
        rows.append(f'  if(rg_count[tid,self->rg_depth]>{i}) printf("A\\t%llu\\t%llu\\t{i}\\t%llx\\n",(uint64_t)tid,(uint64_t)rg_sequence[tid],(uint64_t)rg_arg[tid,self->rg_depth,{i}]);')
    rows.append(' }')
    for i in range(63):
        rows.append(f' if(rg_count[tid,self->rg_depth]>{i}) rg_arg[tid,self->rg_depth,{i}]=0;')
    rows.append(''' rg_desc[tid,self->rg_depth]=0;rg_count[tid,self->rg_depth]=0;
 rg_request[tid,self->rg_depth]=0;rg_params[tid,self->rg_depth]=0;
 self->rg_depth--;rg_returns++;
}''')
    rows.append(f'tick-1s {{ rg_seconds++;if(rg_seconds>={seconds}) exit(0); }}')
    rows.append('dtrace:::ERROR { printf("ERROR\\n"); }')
    rows.append('dtrace:::END { printf("E\\t%llu\\t%llu\\n",(uint64_t)rg_calls,(uint64_t)rg_returns); }')
    return '\n'.join(rows)+'\n'


def summarize(text,stderr,profile):
    requests={};arguments={};errors=[];begin=[];end=[]
    for line in text.splitlines():
        p=line.split('\t')
        if p[0]=='B':begin.append(p[1])
        elif p[0]=='E':end.append(dict(calls=int(p[1]),returns=int(p[2])))
        elif p[0]=='R':
            key=(int(p[1]),int(p[2]))
            if key in requests:errors.append('duplicate request')
            requests[key]=dict(descriptor=int(p[3],16),parameters=int(p[4]),count=int(p[5]),
                               metadata=int(p[6],16),state=int(p[7]),request=int(p[8]))
        elif p[0]=='A':
            key=(int(p[1]),int(p[2]));index=int(p[3])
            args=arguments.setdefault(key,{})
            if index in args:errors.append('duplicate argument')
            args[index]=int(p[4],16)
        elif line.strip():errors.append(line)
    if begin!=[profile['cache_uuid']]:errors.append('cache identity differs or missing')
    if len(end)!=1:errors.append('missing end')
    for line in stderr.splitlines():
        if any(s in line.lower() for s in ('error','drop','invalid','failed')):errors.append(line)
    for key,row in requests.items():
        args=arguments.get(key,{})
        if set(args)!=set(range(row['count'])):errors.append('incomplete key')
        row['arguments']=[args.get(i) for i in range(min(row['count'],63))]
    if arguments.keys()-requests.keys():errors.append('arguments without request')
    return dict(cache_uuid=profile['cache_uuid'],complete=not errors,errors=errors,
                totals=end[0] if end else None,unique_results=len(requests),
                states=dict(Counter(r['state'] for r in requests.values())),
                requests=[r for _,r in sorted(requests.items())],
                scope='Observed canonical runtime requests, deduplicated by result/state. '
                      'Unobserved dependency factories and witness payloads still need capture.')


def main(argv):
    ap=argparse.ArgumentParser(description=__doc__);sub=ap.add_subparsers(dest='action',required=True)
    prep=sub.add_parser('prepare');prep.add_argument('profile',type=Path);prep.add_argument('output',type=Path)
    prep.add_argument('--seconds',type=int,default=30)
    check=sub.add_parser('summarize')
    for name in ('profile','trace','stderr','output'):check.add_argument(name,type=Path)
    a=ap.parse_args(argv);profile=json.loads(a.profile.read_text())
    if a.action=='summarize':
        result=summarize(a.trace.read_text(),a.stderr.read_text(),profile)
        write_json(a.output,result);print(json.dumps({k:v for k,v in result.items() if k!='requests'},indent=2))
        return 0 if result['complete'] else 1
    a.output.mkdir(parents=True,exist_ok=False)
    (a.output/'runtime-graph.d').write_text(generate(profile,a.seconds))
    write_json(a.output/'profile.json',profile)
    source=Path(__file__).resolve().parents[2]/'tools/bind-runtime-probes.c'
    binary=a.output/'bind-runtime-probes'
    from .helper_build import probe
    probe('bind-runtime-probes', binary)
    write_json(a.output/'build.json',provenance([a.profile,Path(__file__),source],operation='swift-runtime-constructor-capture'))
    return 0
