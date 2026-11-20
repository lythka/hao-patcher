"""Derive read-only Swift/ObjC probes from the supplied x86_64 runtime bytes."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import struct
import subprocess
import uuid

from .cache import commands
from .loader_generation import LoaderFamily, sha
from .objc_emit import FamilyReader
from .provider_identity import x86_slice
from .x86 import Disassembler


def symbols(raw, strings):
    result = {}
    for off in range(0, len(raw), 16):
        index, kind, section, desc, address = struct.unpack_from('<IBBHQ', raw, off)
        if address and index < len(strings):
            name = strings[index:strings.index(b'\0', index)].decode('utf-8', errors='replace')
            result[name] = address
    return result


def cached_symbols(family, path):
    cmds = commands(family, dict(family.images)[path])
    vm, _, fileoff, _ = next(struct.unpack_from('<4Q', raw, 24) for _, cmd, raw in cmds
                            if cmd == 0x19 and raw[8:24].rstrip(b'\0') == b'__LINKEDIT')
    symoff, count, stroff, size = next(struct.unpack_from('<4I', raw, 8) for _, cmd, raw in cmds if cmd == 2)
    return symbols(family.read(vm + symoff - fileoff, count * 16),
                   bytes(family.read(vm + stroff - fileoff, size)))


def disk_symbols(path):
    raw = path.read_bytes()
    start, size, _ = x86_slice(raw)
    raw = raw[start:start + size]
    pos, segments = 32, []
    for _ in range(struct.unpack_from('<I', raw, 16)[0]):
        cmd, size = struct.unpack_from('<II', raw, pos)
        if cmd == 2:
            symoff, count, stroff, strsize = struct.unpack_from('<4I', raw, pos + 8)
        if cmd == 0x19:
            segments.append(struct.unpack_from('<4Q', raw, pos + 24))
        pos += size

    def read(address, size):
        vm, _, off, _ = next(s for s in segments if s[0] <= address and address + size <= s[0] + s[3])
        return raw[off + address - vm:off + address - vm + size]

    return symbols(raw[symoff:symoff + 16 * count], raw[stroff:stroff + strsize]), read


def unique_symbol(table, fragment):
    matches = [(name, address) for name, address in table.items() if fragment in name and '.cold.' not in name]
    if len(matches) != 1:
        raise ValueError(f'{fragment}: expected one symbol, found {len(matches)}')
    return matches[0]


def reachable_returns(dis, read, address, end):
    """Follow direct branches, not calls or embedded switch-table bytes.

    Switch-only returns are not inferred. The current runtimes share epilogues
    reachable through their direct default branches. Native coverage stays OPEN.
    """
    todo, seen, returns = [address], set(), set()
    while todo:
        pc = todo.pop()
        while address <= pc < end and pc not in seen:
            seen.add(pc)
            ins = dis.decode(read(pc, min(15, end - pc)), pc)
            if ins is None:
                raise ValueError(f'undecodable instruction at {pc:#x}')
            b = ins.data
            if b == b'\xc3':
                returns.add(pc - address)
                break
            target = None
            if b[0] == 0xeb or 0x70 <= b[0] <= 0x7f:
                target = ins.end + struct.unpack('<b', b[-1:])[0]
            elif b[:1] == b'\xe9' or b[:1] == b'\x0f' and 0x80 <= b[1] <= 0x8f:
                target = ins.end + struct.unpack('<i', b[-4:])[0]
            if target is not None:
                todo.append(target)
            # A noreturn stack-check call can fall through in this conservative
            # walk into a switch table. Privileged/trap bytes end that path.
            if ins.text.strip().startswith(('jmp', 'ud2', 'sti', 'cli', 'hlt', 'int3')):
                break
            pc = ins.end
    if not returns:
        raise ValueError(f'no reachable return in {address:#x}')
    return sorted(returns)


def table_calls(dis, read, pointer, address, size, target):
    """Find calls to libdyld, including cache stubs with slide-encoded GOT slots.

    Probe the caller on both sides of the call. libdyld tail-jumps into dyld,
    whose private implementation symbol may not exist in DTrace's symbol view.
    Offsets come from the supplied cache, never a prior build's disassembly.
    """
    result = []
    for ins in dis.interval(bytes(read(address, size)), address)[0]:
        if ins.data[:2] == b'\xff\x15':
            if pointer(ins.rip_target) == target:
                result.append(dict(call=ins.address-address, resume=ins.end-address))
            continue
        if ins.data[:1] != b'\xe8':
            continue
        callee = ins.direct_target
        if callee != target:
            stub = dis.decode(read(callee, 6), callee)
            if stub is None or stub.data[:2] != b'\xff\x25' or pointer(stub.rip_target) != target:
                continue
        result.append(dict(call=ins.address - address, resume=ins.end - address))
    if not result:
        raise ValueError('cannot locate Swift call to libdyld pointer-table lookup')
    return result


def original_lookup_read(read, recipe):
    """Decode the original body's CFG without changing any installed bytes.

    This is only the original body's analysis view. A redirected lookup must
    pair its entry with the wrapper's return: the original returns one stack
    frame below the caller of the redirect.
    """
    address=recipe['profile']['metadata_lookup']
    edit=next(row for row in recipe['byte_edits'] if row['address']==address)
    before,after=bytes.fromhex(edit['before']),bytes.fromhex(edit['after'])
    if len(before)!=len(after) or read(address,len(after))!=after:
        raise ValueError('runtime recipe does not match installed metadata redirect')
    def restored(start,size):
        data=bytearray(read(start,size))
        lo,hi=max(start,address),min(start+size,address+len(before))
        if lo<hi:data[lo-start:hi-start]=before[lo-address:hi-address]
        return bytes(data)
    return restored


def profile(cache, dyld, catalog=None, runtime_recipe=None):
    family, dis = LoaderFamily(cache), Disassembler()
    try:
        swift = cached_symbols(family, '/usr/lib/swift/libswiftCore.dylib')
        objc = cached_symbols(family, '/usr/lib/libobjc.A.dylib')
        libdyld = cached_symbols(family, '/usr/lib/system/libdyld.dylib')
        ds, disk_read = disk_symbols(dyld)
        rows = {}

        def function(label, table, fragment, read, module, returns=True):
            name, start = unique_symbol(table, fragment)
            end = min(a for a in table.values() if a > start)
            data = bytes(read(start, end - start))
            rows[label] = dict(symbol=name[1:], address=start, size=len(data), module=module,
                               sha256=hashlib.sha256(data).hexdigest(),
                               returns=reachable_returns(dis, read, start, end) if returns else [])
            return start, data

        callback, data = function('callback', swift, '28libPrespecializedImageLoadedEv', family.read, 'libswiftCore.dylib')
        stores = [i for i in dis.interval(data, callback)[0] if i.data[:2] == b'\xc7\x05' and i.data[-4:] == b'\x04\0\0\0']
        if len(stores) != 1:
            raise ValueError('cannot locate callback disabled-state store')
        mode = stores[0].rip_target
        recipe=json.loads(runtime_recipe.read_text()) if runtime_recipe else None
        lookup_read=original_lookup_read(family.read,recipe) if recipe else family.read
        lookup, data = function('lookup', swift, '28getLibPrespecializedMetadataE', lookup_read, 'libswiftCore.dylib')
        rows['lookup']['installed_sha256']=hashlib.sha256(family.read(lookup,len(data))).hexdigest()
        table, _ = function('table', libdyld, '__dyld_find_pointer_hash_table_entry', family.read, 'libdyld.dylib', False)
        calls = table_calls(dis, lookup_read, FamilyReader(family).ptr, lookup, len(data), table)
        if recipe:
            wrapper=recipe['families']['metadata']['wrapper']
            blob=next(b for b in recipe['blobs'] if b['address']<=wrapper<b['address']+len(b['data'])//2)
            end=blob['address']+len(blob['data'])//2
            data=bytes(family.read(wrapper,end-wrapper))
            expected=bytes.fromhex(blob['data'])[wrapper-blob['address']:]
            if data!=expected:
                raise ValueError('runtime recipe does not match installed metadata wrapper')
            rows['lookup_original']=rows['lookup']
            rows['lookup']=dict(symbol='hao_metadata_lookup',address=wrapper,size=len(data),
                module='injected runtime',sha256=hashlib.sha256(data).hexdigest(),
                returns=reachable_returns(dis,family.read,wrapper,end))
            calls=[{k:v+lookup-wrapper for k,v in c.items()} for c in calls]
            calls+=table_calls(dis,family.read,FamilyReader(family).ptr,wrapper,len(data),table)
        wrapper = libdyld['_dyld_shared_cache_some_image_overridden']
        loads = [i for i in dis.interval(family.read(wrapper, 20), wrapper)[0] if i.data[:3] == b'\x48\x8b\x3d']
        gapis = loads[0].rip_target
        override, data = function('override', ds, 'APIs39dyld_shared_cache_some_image_overridden', disk_read, 'dyld')
        reads = [i for i in dis.interval(data, override)[0] if i.data[:3] == b'\x0f\xb6\x9f']
        if len(reads) != 1:
            raise ValueError('cannot locate dyld override byte read')
        field = struct.unpack_from('<i', reads[0].data, 3)[0]
        function('objc_map', objc, '_map_images_nolock', family.read, 'libobjc.A.dylib', False)
        function('objc_read_class', objc, '9readClassP10objc_classbb', family.read, 'libobjc.A.dylib', False)
        function('objc_categories', objc, '__ZL22load_categories_nolockP11header_info', family.read, 'libobjc.A.dylib', False)
        function('objc_attach', objc, '__ZL16attachCategoriesP', family.read, 'libobjc.A.dylib', False)
        globals_ = {key: unique_symbol(objc, name)[1] for key, name in {
            'preoptimized': '__ZL12preoptimized', 'disable': '_DisablePreopt',
            'disable_caches': '_DisablePreoptCaches',
            'method_total': 'E22PreoptTotalMethodLists', 'method_optimized': 'E26PreoptOptimizedMethodLists',
            'class_total': 'E18PreoptTotalClasses', 'class_optimized': 'E22PreoptOptimizedClasses',
        }.items() if key != 'disable'}
        globals_['disable'] = objc['_DisablePreopt']
        catalog_rows = []; witness_rows = []
        base = family.main.mappings[0].address
        generic = swift['_swift_getGenericMetadata']
        generic_target = generic
        visited = set()
        while generic_target not in visited:
            visited.add(generic_target)
            end = min(a for a in swift.values() if a > generic_target)
            instructions = dis.interval(family.read(generic_target, end-generic_target), generic_target)[0]
            jumps = [i.direct_target for i in instructions if i.data[:1] == b'\xe9']
            if not any(i.data == b'\xc3' for i in instructions) and len(jumps) == 1:
                generic_target = jumps[0]
                continue
            rows['generic_return'] = dict(symbol=next(n[1:] for n,a in swift.items() if a == generic_target),
                module='libswiftCore.dylib',address=generic_target,
                returns=reachable_returns(dis, family.read, generic_target, end))
            break
        if 'generic_return' not in rows:
            raise ValueError('cannot locate generic metadata implementation return')
        if catalog:
            from .swift_installed import records as installed_records
            records, _ = installed_records(json.loads(catalog.read_text()), family)
            witness_rows = [r for r in records if r['kind']=='protocol-witness']
            for index, row in enumerate((r for r in records if r['kind']=='metadata'), 1):
                catalog_rows.append(dict(index=index, id=row.get('id', row['name']), name=row['name'], key=[base + x for x in row['key']],
                                         metadata=row['address'] if row['existing'] else None,
                                         **({'abi_family':row['abi_family']} if 'abi_family' in row else {})))
        return dict(schema=1, cache_uuid=str(uuid.UUID(bytes=bytes(family.main.data[88:104]))),
                    cache_base=family.main.mappings[0].address, mode=mode, gapis=gapis,
                    override_field=field, functions=rows, table_calls=calls, objc=globals_,
                    generic_address=generic, catalog=catalog_rows, witnesses=witness_rows,
                    lookup_scope=('Installed wrapper result including exact and conformance-normalized tables.' if recipe
                                  else 'Original exact-pointer lookup.'),
                    inputs=[dict(path=str(f.path.resolve()), sha256=sha(f.path)) for f in family.files]
                           + [dict(path=str(dyld.resolve()), sha256=sha(dyld))]
                           + ([dict(path=str(catalog.resolve()), sha256=sha(catalog))] if catalog else [])
                           + ([dict(path=str(runtime_recipe.resolve()),sha256=sha(runtime_recipe))] if runtime_recipe else []))
    finally:
        dis.close()
        family.close()


def generate(p):
    """Appendable to netboot/swift-requests.d; no timers, exits or runtime writes."""
    f = p['functions']
    def probe(name, where='entry'):
        r = f[name]
        offset = 0 if where == 'entry' else int(where, 16)
        return f'pid$target::-:CACHEADDR_{r["address"]+offset:x}_END'
    def read(address, width=8):
        return f'*(uint{width * 8}_t*)copyin(cr_slide+{address}, {width})'
    mode = read(p['mode'], 4)
    override = f'*(uint8_t*)copyin({read(p["gapis"])}+{p["override_field"]},1)'
    rows = ['/* Generated cache-runtime probes. SQ/SA/SR/ST/SU/SI/OS/OC are separate from bench records. */']
    # Catalog matching is offline. Embedding hundreds of rows in DTrace made
    # the loaded DOF exceed its kernel size limit, even with small DIF actions.
    rows.append('pid$target::swift_getGenericMetadata:entry' +
                f'\n{{ cr_slide=uregs[R_RIP]-{p["generic_address"]}; }}')
    for label in ('lookup', 'callback', 'objc_map', 'objc_categories'):
        rows.append(probe(label) + f'\n{{ cr_slide=uregs[R_RIP]-{f[label]["address"]}; }}')
    # Print the mapped UUID, rather than assuming that the artifact is installed.
    rows.append('pid$target::swift_getGenericMetadata:entry,\n' + probe('lookup') + f'''\n/!cr_identity/
{{ cr_identity=1; printf("SB\\t%llu\\t%llx\\t%llx\\t%llx\\n",(uint64_t)walltimestamp,
    (uint64_t)cr_slide,{read(p['cache_base'] + 88)},{read(p['cache_base'] + 96)}); }}''')
    rows.append(probe('lookup') + f'''\n{{
 if (!self->cr_depth) self->cr_depth=1;
 self->cr_depth++; self->cr_seq++;
 cr_id[tid,self->cr_depth]=self->cr_seq;
 cr_sp[tid,self->cr_depth]=uregs[R_RSP];
 self->cr_flags=*(uint32_t*)copyin(uregs[R_RDI],4); self->cr_kind=self->cr_flags&31;
 self->cr_n=65535; self->cr_args=uregs[R_RSI];
 if (self->cr_flags&128) {{
  if (self->cr_kind==16) self->cr_n=*(uint16_t*)copyin(uregs[R_RDI]+56,2);
  if (self->cr_kind==17 || self->cr_kind==18) self->cr_n=*(uint16_t*)copyin(uregs[R_RDI]+40,2);
 }}
 printf("SQ\\t%llu\\t%llu\\t%llu\\t%u\\t%u\\t%llx\\t%u\\n",
 (uint64_t)walltimestamp,(uint64_t)tid,(uint64_t)self->cr_seq,{mode},{override},(uint64_t)uregs[R_RDI],(uint32_t)self->cr_n);
}}''')
    for i in range(63):
        rows.append(probe('lookup') + f'''\n/self->cr_n>{i} && self->cr_n<=63/
{{ printf("SA\\t%llu\\t%llu\\t{i}\\t%llx\\n",(uint64_t)tid,(uint64_t)self->cr_seq,
 *(uint64_t*)copyin(self->cr_args+{i*8},8)); }}''')
    for offset in f['lookup']['returns']:
        rows.append(probe('lookup', f'{offset:x}') + f'''\n/self->cr_depth>1 && cr_sp[tid,self->cr_depth]==uregs[R_RSP]/
{{ printf("SR\\t%llu\\t%llu\\t%llu\\t%u\\t%u\\t%llx\\n",(uint64_t)walltimestamp,
 (uint64_t)tid,(uint64_t)cr_id[tid,self->cr_depth],{mode},{override},(uint64_t)uregs[R_RAX]);
 self->cr_depth--; }}''')
    # Offset probes use registers, not entry-probe argN semantics. The stack is
    # equal before CALL and after its return (including libdyld's tail jump).
    for call in p['table_calls']:
        rows.append(probe('lookup', f'{call["call"]:x}') + '''\n/self->cr_depth>1/
{ cr_t_id[tid,self->cr_depth]=cr_id[tid,self->cr_depth];
 cr_t_sp[tid,self->cr_depth]=uregs[R_RSP];
 printf("ST\\t%llu\\t%llu\\t%llu\\t%llx\\t%llx\\t%llu\\n",(uint64_t)walltimestamp,
 (uint64_t)tid,(uint64_t)cr_id[tid,self->cr_depth],
 (uint64_t)uregs[R_RDI],(uint64_t)uregs[R_RSI],(uint64_t)uregs[R_RDX]); }''')
        rows.append(probe('lookup', f'{call["resume"]:x}') + '''\n/self->cr_depth>1 &&
 cr_t_id[tid,self->cr_depth]==cr_id[tid,self->cr_depth] && cr_t_sp[tid,self->cr_depth]==uregs[R_RSP]/
{ printf("SU\\t%llu\\t%llu\\t%llu\\t%llx\\n",(uint64_t)walltimestamp,(uint64_t)tid,
 (uint64_t)cr_t_id[tid,self->cr_depth],(uint64_t)uregs[R_RAX]);
 @cr_table[pid,execname,uregs[R_RAX] ? "hit" : "miss"]=count();
 cr_t_id[tid,self->cr_depth]=-1; }''')
    for where in ['entry', *[f'{o:x}' for o in f['callback']['returns']]]:
        rows.append(probe('callback', where) + f'''\n{{ printf("SI\\t%llu\\t%llu\\t{where}\\t%u\\t%u\\n",
 (uint64_t)walltimestamp,(uint64_t)tid,{mode},{override}); }}''')
    for label in ('objc_map', 'objc_read_class', 'objc_categories', 'objc_attach'):
        rows.append(probe(label) + f'''\n{{ @cr_objc["{label}"]=count(); }}''')
    g = p['objc']
    args = [read(g[n], 1 if n in ('preoptimized', 'disable', 'disable_caches') else 4) for n in g]
    fmt = '\\t%u' * len(args)
    rows.append(probe('objc_categories') + ',\n' + probe('lookup') + f'''\n/timestamp-cr_last_snapshot>1000000000/
{{ cr_last_snapshot=timestamp; printf("OS\\t%llu{fmt}\\n",(uint64_t)walltimestamp,{','.join(args)}); }}''')
    rows.append('dtrace:::END { printa("OC\\t%s\\t%@d\\n",@cr_objc); printf("SE\\n"); }')
    rows.append('tick-1sec, dtrace:::END { printa("H\\t%d\\t%s\\t%s\\t%@d\\n",@cr_table); }')
    return '\n\n'.join(rows) + '\n'


def summarize(trace, p, stderr=''):
    records = [line.split('\t') for line in trace.splitlines() if line.startswith(('SB\t','SQ\t','SA\t','SR\t','ST\t','SU\t','SI\t','OS\t','OC\t'))]
    requests, identities, callbacks, snapshots, objc = {}, [], [], [], {}
    orphaned = 0
    # Per-CPU buffers may place argument/return rows before entry rows.
    for r in records:
        if r[0] == 'SQ':
            key = int(r[2]), int(r[3])
            if key in requests:
                raise ValueError('duplicate request')
            requests[key] = dict(wall=int(r[1]), mode_entry=int(r[4]), override_entry=int(r[5]),
                                 descriptor=int(r[6],16), count=int(r[7]), arguments={}, table=[], table_events=[], result=None)
        elif r[0] == 'SB':
            mapped = uuid.UUID(bytes=struct.pack('<QQ',int(r[3],16),int(r[4],16)))
            identities.append(dict(wall=int(r[1]), slide=int(r[2],16), cache_uuid=str(mapped)))
        elif r[0] == 'SI':
            callbacks.append(dict(wall=int(r[1]), tid=int(r[2]), point=r[3], mode=int(r[4]), override=int(r[5])))
        elif r[0] == 'OS':
            snapshots.append(dict(wall=int(r[1]), **dict(zip(p['objc'],map(int,r[2:])))))
        elif r[0] == 'OC':
            objc[r[1]] = int(r[2])
    for r in records:
        if r[0] not in ('SA','SR','ST','SU'):
            continue
        key = (int(r[1]),int(r[2])) if r[0]=='SA' else (int(r[2]),int(r[3]))
        if key not in requests:
            orphaned += 1
            continue
        q = requests[key]
        if r[0] == 'SA':
            index = int(r[3])
            if index in q['arguments']:
                raise ValueError('duplicate argument')
            q['arguments'][index] = int(r[4],16)
        elif r[0] == 'SR':
            if q['result'] is not None:
                raise ValueError('duplicate request return')
            q.update(mode_return=int(r[4]),override_return=int(r[5]),result=int(r[6],16))
        elif r[0] == 'ST':
            q['table_events'].append((int(r[1]), 'call', dict(address=int(r[4],16),descriptor=int(r[5],16),count=int(r[6]))))
        else:
            q['table_events'].append((int(r[1]), 'return', int(r[4],16)))
    counts, modes = Counter(), Counter()
    for q in requests.values():
        modes[str(q['mode_entry'])] += 1
        # A wrapper can consult a second map after an exact-key miss. Pair
        # every call/return in timestamp order, including cross-CPU buffers.
        pending = None
        table_complete = True
        for wall, kind, value in sorted(q.pop('table_events'), key=lambda e:(e[0], e[1])):
            if kind == 'call':
                if pending is not None:
                    table_complete = False
                pending = value
                q['table'].append(value)
            elif pending is None:
                table_complete = False
            else:
                pending['result'] = value
                pending = None
        table_complete = table_complete and pending is None
        if q['table']:
            q['table_result'] = q['table'][-1].get('result')
        if not table_complete or q['result'] is None or set(q['arguments']) != set(range(q['count'])):
            outcome='incomplete'
        elif q['table']:
            t=q['table'][0]
            if (any(t['descriptor']!=q['descriptor'] or t['count']!=q['count'] for t in q['table']) or
                    any(t.get('result') != 0 for t in q['table'][:-1]) or
                    q.get('table_result')!=q['result']):
                outcome='incomplete'
            else:
                outcome='hit' if q['result'] else 'miss'
        elif q['result']:
            outcome='non_dyld_result'
        else:
            outcome='bypass'
        q['outcome']=outcome
        q['arguments']=[q['arguments'].get(i) for i in range(min(q['count'],63))]
        counts[outcome]+=1
    errors=[line for line in trace.splitlines() if line.startswith(('E\t','ERROR'))]
    errors += [line for line in stderr.splitlines() if re.search(r'drop|error|invalid|failed',line,re.I)]
    if orphaned:
        errors.append(f'{orphaned} orphaned records (request entry missing)')
    complete=(bool(identities) and all(i['cache_uuid']==p['cache_uuid'] for i in identities)
              and 'SE' in trace.splitlines() and not errors and not counts['incomplete'] and bool(requests))
    catalog = catalog_summary(trace, p, requests.values(), identities, complete)
    return dict(complete=complete, identities=identities, errors=errors, counts=dict(counts), catalog=catalog,
                entry_modes=dict(modes), callbacks=sorted(callbacks,key=lambda r:r['wall']),
                objc_calls=objc, objc_snapshots=sorted(snapshots,key=lambda r:r['wall']),
                requests=list(requests.values()),
                limits=['Instrumented observations, not uninstrumented timings.',
                        'Cache-range pointers may be process-local allocations; these keys are not serializable metadata.',
                        'ObjC readClass/category calls are work counts, not cache misses or IMP-cache hit rates.',
                        'An enabled optimization is not proof of an application speedup.'])


def catalog_summary(trace, p, requests, identities, complete):
    """Exact table keys and canonical result addresses, not runtime-cache membership."""
    rows = [dict(r, lookups=dict(hit=0, miss=0, bypass=0, incomplete=0, non_dyld_result=0, mismatch=0),
                 returns=dict(catalog=0, runtime=0, other_shared=0, null=0)) for r in p.get('catalog', [])]
    by_index = {r['index']: r for r in rows}
    by_key = {tuple(r['key']): r for r in rows if r['metadata'] is not None}
    slides = {r['slide'] for r in identities}
    if len(slides) == 1:
        slide = slides.pop()
        for q in requests:
            if q['count'] != len(q['arguments']) or any(a is None for a in q['arguments']):
                continue
            row = by_key.get(tuple(a-slide for a in [q['descriptor']]+q['arguments']))
            if row:
                row['lookups'][q['outcome']] += 1
                if q['outcome'] == 'hit' and q['result'] - slide != row['metadata']:
                    row['lookups']['mismatch'] += 1
    if len({r['slide'] for r in identities}) == 1 and not any(line.startswith('CG\t') for line in trace.splitlines()):
        by_result = {(r['key'][0], r['metadata']): r for r in rows if r['metadata'] is not None}
        for line in trace.splitlines():
            parts = line.split('\t')
            if parts[0] == 'N' and len(parts) >= 10:
                row = by_result.get((int(parts[4],16)-slide, int(parts[5],16)-slide))
                if row:
                    row['returns']['catalog'] += 1
    for line in trace.splitlines():
        if line.startswith('CG\t'):
            _, index, outcome, count = line.split('\t')
            if int(index) in by_index:
                by_index[int(index)]['returns'][outcome] += int(count)
    totals = {kind: sum(r['returns'][kind] for r in rows) for kind in ('catalog','runtime','other_shared','null')}
    totals['table_hits'] = sum(r['lookups']['hit'] for r in rows)
    totals['table_misses'] = sum(r['lookups']['miss'] for r in rows)
    totals['mismatch'] = sum(r['lookups']['mismatch'] for r in rows)
    return dict(return_matching='legacy_full_key' if any(line.startswith('CG\t') for line in trace.splitlines()) else
                'canonical_address (noncanonical returns classified only by full-key lookup events)',
                complete=complete and 'GE' in trace.splitlines() and bool(rows) and
                all(r['metadata'] is not None for r in rows), totals=totals, entries=rows,
                observed=sum(any(r['returns'].values()) or any(r['lookups'].values()) for r in rows),
                unobserved=sum(not any(r['returns'].values()) and not any(r['lookups'].values()) for r in rows))


def verify_capture(p, recipe, captured):
    """Exercise every named entry in an ordinary helper, without forcing lookup gates."""
    header, *observations = captured
    if header.get('schema') == 2:
        return verify_graph_capture(p, header, observations)
    identity = header['cache_uuid'] == p['cache_uuid']
    slide = header['base'] - p['cache_base']
    expected = {r['name']: r for r in p['catalog']}
    seen = {r['index']: r for r in observations}
    entries = []
    for index, requested in enumerate(recipe['records'], 1):
        observed, target = seen.get(index), expected.get(requested['name'])
        outcome = 'unobserved'
        if observed:
            outcome = 'mismatch'
            if identity and target and target['metadata'] is not None and observed.get('none_copy_destroy'):
                outcome = ('catalog' if observed['metadata'] - slide == target['metadata'] else
                           'runtime' if observed['metadata'] < header['base'] else 'other_shared')
        entries.append(dict(name=requested['name'], outcome=outcome))
    counts = dict(Counter(r['outcome'] for r in entries))
    complete = (identity and set(expected) == {r['name'] for r in entries} and
                len(observations) == len(entries) == len(seen) and counts.get('catalog') == len(entries))
    return dict(complete=complete, cache_uuid=header['cache_uuid'], counts=counts, entries=entries,
                scope='Isolated helper adoption; ordinary app coverage and performance are separate.')


def verify_graph_capture(p, header, observations):
    """Canonical addresses for every installed entry, including recursive captures."""
    slide = header['base']-p['cache_base']
    identity = header['cache_uuid']==p['cache_uuid']
    by_name = {}
    for row in observations:
        address = row.get('node') if row.get('status')=='captured' else row.get('metadata') if 'root' in row else None
        if address:
            by_name.setdefault(row['name'], set()).add(address-slide)
    expected = {}
    for row in p['catalog']:
        if row['metadata'] is not None:
            expected.setdefault(row['name'], set()).add(row['metadata'])
    entries=[]
    for row in p['catalog']:
        observed=by_name.get(row['name'], set())
        outcome='unobserved'
        if observed:
            outcome='mismatch'
            if identity and row['metadata'] is not None:
                if row['metadata'] in observed:
                    outcome='catalog'
                elif observed <= expected.get(row['name'], set()):
                    outcome='unselected_variant'
        entries.append(dict(id=row.get('id',row['name']),name=row['name'],outcome=outcome))
    counts=dict(Counter(r['outcome'] for r in entries))
    finished=identity and bool(entries) and observations[-1:]==[dict(complete=True)]
    return dict(complete=finished and counts.get('catalog')==len(entries),
                names_complete=finished and all(by_name.get(r['name']) and
                    by_name[r['name']] <= expected.get(r['name'],set()) for r in p['catalog']),
                cache_uuid=header['cache_uuid'],counts=counts,entries=entries,
                scope='Isolated helper adoption. Unselected variants need separate exact-key exercise; app use is separate.')


def verify_capture_main(argv):
    ap = argparse.ArgumentParser(description='Compare every captured result with its installed catalog address.')
    ap.add_argument('profile', type=Path)
    ap.add_argument('recipe', type=Path)
    ap.add_argument('capture', type=Path)
    ap.add_argument('output', type=Path)
    a = ap.parse_args(argv)
    result = verify_capture(json.loads(a.profile.read_text()), json.loads(a.recipe.read_text()),
                            [json.loads(line) for line in a.capture.read_text().splitlines()])
    a.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({k:v for k,v in result.items() if k != 'entries'}))
    if not result['complete']:
        raise SystemExit(1)


def prepare_main(argv):
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('cache',type=Path)
    ap.add_argument('dyld',type=Path)
    ap.add_argument('output',type=Path)
    ap.add_argument('--swift-catalog',type=Path,help='catalog installed in this cache; emit per-entry counts')
    ap.add_argument('--runtime-recipe',type=Path,help='installed witness_runtime recipe for a redirected metadata lookup')
    a=ap.parse_args(argv)
    p=profile(a.cache,a.dyld,a.swift_catalog,a.runtime_recipe)
    a.output.mkdir(parents=True)
    (a.output/'profile.json').write_text(json.dumps(p,indent=2)+'\n')
    (a.output/'runtime.d').write_text(generate(p))
    if a.swift_catalog:
        from .swift_graph import verification_recipe
        (a.output/'verification-recipe.json').write_text(json.dumps(verification_recipe(
            json.loads(a.swift_catalog.read_text()),p),indent=2)+'\n')
    root=Path(__file__).resolve().parents[2]
    source=root/'tools/objc-runtime-probe.m'
    binary=a.output/'objc-probe'
    subprocess.run(['xcrun','clang','-arch','x86_64','-mmacosx-version-min=14.0',
                    '-fobjc-arc','-O2','-Wall','-Wextra','-Werror','-framework','Foundation',
                    str(source),'-o',str(binary)],check=True)
    subprocess.run(['codesign','-s','-',str(binary)],check=True)
    binder=a.output/'bind-runtime-probes'
    subprocess.run(['xcrun','clang','-arch','x86_64','-mmacosx-version-min=14.0',
                    '-O2','-Wall','-Wextra','-Werror',
                    str(root/'tools/bind-runtime-probes.c'),'-o',str(binder)],check=True)
    subprocess.run(['codesign','-s','-',str(binder)],check=True)
    from .swift_metadata import provenance, write_json
    inputs=[Path(row['path']) for row in p['inputs']]+[Path(__file__),source,
            root/'tools/bind-runtime-probes.c']
    write_json(a.output/'build.json',provenance(inputs,operation='runtime-probes',cache_uuid=p['cache_uuid']))
    print(a.output/'runtime.d')


def summarize_main(argv):
    ap=argparse.ArgumentParser(description='Summarize appended runtime probes from a bench trace.')
    ap.add_argument('profile',type=Path)
    ap.add_argument('trace',type=Path)
    ap.add_argument('output',type=Path)
    ap.add_argument('--stderr',type=Path)
    a=ap.parse_args(argv)
    result=summarize(a.trace.read_text(),json.loads(a.profile.read_text()),a.stderr.read_text() if a.stderr else '')
    a.output.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k not in ('requests','callbacks','objc_snapshots')}))
