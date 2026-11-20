"""Serialize ABI-described generic value metadata and its owned dependencies.

Discovery reports unsupported roots explicitly. The existing catalog is retained;
only complete dependency closures are added. Heap witnesses/classes/packs are not
guessed from pointer-shaped words.
"""
import hashlib
from functools import lru_cache
import itertools
import json
import re
import struct
import uuid

from .exports import Refusal, require


def framework_owners(family):
    """Read module owners from this cache's Swift type records, not an app list."""
    from .cache import commands
    from .objc_emit import FamilyReader
    reader=FamilyReader(family)
    owners={}
    def relative(address, indirect=False):
        value=struct.unpack('<i',family.read(address,4))[0]
        if not value:
            return 0
        target=address+(value & ~1 if indirect else value)
        return reader.ptr(target) if indirect and value & 1 else target
    def module(address):
        seen=set()
        while address and address not in seen:
            seen.add(address)
            kind=struct.unpack('<I',family.read(address,4))[0]&31
            if kind==0:
                name=relative(address+8)
                return bytes(family.read(name,256)).split(b'\0',1)[0].decode()
            address=relative(address+4,True)
        return None
    for path,header in family.images:
        for _,cmd,raw in commands(family,header):
            if cmd!=0x19:
                continue
            for i in range(struct.unpack_from('<I',raw,64)[0]):
                section=raw[72+i*80:152+i*80]
                if section[:16].rstrip(b'\0')!=b'__swift5_types':
                    continue
                start,size=struct.unpack_from('<QQ',section,32)
                for offset in range(0,size,4):
                    value=struct.unpack('<i',family.read(start+offset,4))[0]
                    if value & 3 > 1:
                        continue
                    target=start+offset+(value & ~3)
                    if value & 1:
                        target=reader.ptr(target)
                    if target:
                        name=module(target)
                        if name:
                            owners.setdefault(name,set()).add(path)
    result={}
    for name,paths in sorted(owners.items()):
        native=[p for p in paths if not p.startswith('/System/iOSSupport/')]
        candidates=native or sorted(paths)
        primary=[p for p in candidates if p.rsplit('/',1)[-1] in (name,'libswift'+name+'.dylib')]
        result[name]=sorted(primary or candidates)
    return result


def optional_records(catalog):
    if catalog['kind'] == 'swift-value-catalog':
        return catalog['records']
    require(catalog['kind'] == 'swift-optional-catalog', 'unknown base Swift catalog')
    result = []
    for row in catalog['records']:
        words = [0] * 20
        words[2] = 0x202
        words[14:17] = row['witness_data']
        pointers = [dict(offset=8, ref=dict(local=48)), dict(offset=24, ref=row['descriptor']),
                    dict(offset=32, ref=row['argument'])]
        pointers += [dict(offset=48+8*int(slot), ref=ref) for slot, ref in row['functions'].items()]
        result.append(dict(name=row['name'], key=[row['descriptor'], row['argument']],
                           data=struct.pack('<20Q', *words).hex(), metadata_offset=16, pointers=pointers))
    return result


def record_id(row):
    return row.get('id', row['name'])


def key_identity(key):
    return json.dumps(key, sort_keys=True, separators=(',', ':'))


def installed_identities(records, family, refs, *, by_id=False):
    """Recognize retained generated dependencies before encoding image sections."""
    from .swift_metadata import pointer_entries
    table, roots = pointer_entries(family)
    from .swift_installed import auxiliary_entries
    witnesses = auxiliary_entries(family, roots) if any(
        r.get('record_kind') in ('protocol-witness','metatype') for r in records) else {}
    pending = {record_id(r): r for r in records}
    resolved = {}
    while pending:
        ready = [i for i, row in pending.items()
                 if all('owned' not in ref or ref['owned'] in resolved for ref in row['key'])]
        if not ready:
            break  # Missing dependencies are not installed in this source cache.
        for identity in ready:
            row = pending.pop(identity)
            key = tuple((resolved[ref['owned']] if 'owned' in ref else refs.decode(ref)) - roots['base']
                        for ref in row['key'])
            lookup = witnesses if row.get('record_kind') in ('protocol-witness','metatype') else table
            values = list(lookup.get(key, []))
            if len(values) == 1:
                resolved[identity] = roots['base'] + values[0]
    return resolved if by_id else {address: identity for identity, address in resolved.items()}


def verification_recipe(catalog, profile=None):
    """Resolve semantic names while retaining their original module dependencies."""
    records = catalog['records'] if catalog.get('kind')=='swift-dependency-catalog' else optional_records(catalog)
    by_id = {record_id(r): r for r in records}
    def owner(row):
        if row.get('owner'):
            return row['owner']
        argument = row['key'][1]
        return owner(by_id[argument['owned']]) if 'owned' in argument else argument['image']
    roots = {r['name']: dict(name=r['name'], owner=owner(r)) for r in records}
    # Exact-key verification needs only installed payload owners. Discovery
    # loads can also contain owners of unresolved or foreign-platform recipes.
    loads = set() if profile is not None else set(catalog.get('loads', []))
    loads.update(owner(r) for r in records)
    loads.update(ref['image'] for row in records+catalog.get('witnesses', []) for ref in row['key']+[p['ref'] for p in row['pointers']]
                 if 'image' in ref)
    recipe = dict(schema=1, kind='generic-values', loads=sorted(loads), records=list(roots.values()))
    if profile is not None:
        require(all(r['metadata'] is not None for r in profile['catalog']), 'verification catalog is not fully installed')
        recipe['cache_uuid'] = profile['cache_uuid']
        recipe['installed_metadata'] = sorted({r['metadata']-profile['cache_base'] for r in profile['catalog']})
        recipe['records'] = [dict(roots[r['name']], id=r.get('id',r['name']),
                                  key=[p-profile['cache_base'] for p in r['key']]) for r in profile['catalog']]
    return recipe


def layout_references(blob):
    from .swift_layout import references
    return references(blob)


def normalize_graph(recipe, captured, family, base_catalog, *, witness_closures=False):
    if 'installed_metadata' in recipe:
        from .swift_installed import verify_capture
        return verify_capture(recipe,captured,family,base_catalog)
    from .swift_metadata import References
    from .swift_abi import export, schemas
    header, *events = captured
    require(header['schema'] == 2 and header['cache_uuid'] == str(uuid.UUID(bytes=bytes(family.main.data[88:104]))),
            'Swift graph capture did not run with the supplied cache')
    prefix = bool(header.get('recovered_prefix'))
    require(events and events[-1] == (dict(prefix_complete=True) if prefix else dict(complete=True)),
            'Swift graph capture was interrupted')
    nodes = {r['node']: r for r in events if 'node' in r}
    roots = [r for r in events if 'root' in r]
    require(len(roots) == len(recipe['records']) and
            all(r['root'] == i and r['name'] == q['name'] for i,(r,q) in enumerate(zip(roots, recipe['records']),1)),
            'Swift graph capture lost or reordered requested roots')
    refs = References(family)
    slide = header['base'] - family.main.mappings[0].address
    dependency_base = base_catalog.get('kind') == 'swift-dependency-catalog'
    if dependency_base:
        require(base_catalog.get('schema') == 4, 'unknown dependency catalog schema')
        base_records = base_catalog['records'] + base_catalog['witnesses']
        witness_closures = True
    else:
        base_records = optional_records(base_catalog)
    merged = {record_id(r): r for r in base_records}
    by_key = {key_identity(r['key']): record_id(r) for r in base_records
              if r.get('record_kind') != 'protocol-witness'}
    installed_by_id = installed_identities(base_records, family, refs, by_id=True)
    installed = {address:identity for identity,address in installed_by_id.items()}
    @lru_cache(maxsize=None)
    def owned_identity(identity):
        if identity in installed_by_id:
            return 'address',installed_by_id[identity]
        if identity not in merged:
            return 'owned',identity
        # Existing aliases may themselves have image/owned spellings in their
        # keys. Compare their constructor identities transitively, not their IDs.
        signature=key_identity(bound_key(merged[identity]))
        return 'constructor',hashlib.sha256(signature.encode()).hexdigest()
    def bound_key(row):
        # An image reference and a retained owned reference can bind to the same
        # source object. Compare them at binding, without rewriting portable
        # records or introducing process/cache addresses into catalog identity.
        def operand(ref):
            if 'owned' in ref:
                return owned_identity(ref['owned'])
            return ('address',refs.decode(ref))
        return row.get('record_kind','generic-value'),tuple(operand(ref) for ref in row['key'])
    retained_keys={}
    for row in base_records:
        retained_keys.setdefault(bound_key(row),row)
    witness_nodes = {}
    if witness_closures:
        for row in schemas(captured):
            witness_nodes.setdefault(row['protocol_witness'], []).append(row)
    ready, blocked, visiting = {}, {}, set()

    def reference(pointer, executable=False, *, owner=None, address_point=0):
        # A typed data edge back to its own address point is an intra-record
        # relocation, not another constructor dependency. Associated-conformance
        # witness slots can canonically refer to the table containing the slot.
        if not executable and pointer == owner:
            return dict(local=address_point)
        if not executable and pointer-slide in installed:
            return dict(owned=installed[pointer-slide])
        if header['base'] <= pointer < header['end']:
            try:
                return refs.encode(pointer-slide, executable=executable)
            except Refusal:
                pass  # Shared-region reservations also contain process-local metadata.
        require(not executable, 'process-local witness function')
        dependency = convert(pointer)
        return dict(owned=record_id(dependency))

    def convert(address):
        if address-slide in installed:
            return merged[installed[address-slide]]
        if address in ready:
            return ready[address]
        if address in blocked:
            raise blocked[address]
        require(address not in visiting, 'cyclic metadata dependency')
        visiting.add(address)
        try:
            if address in witness_nodes:
                candidates=witness_nodes[address]
                require(len({(r['descriptor'],r['type']) for r in candidates})==1,
                        'process-local witness has multiple constructor keys')
                row=candidates[0]
                result=export('protocol-witness',row,address,reference,refs,slide)
                retained=retained_keys.get(bound_key(result))
                if retained is not None:
                    ready[address]=retained
                    return retained
                signature=key_identity(result['key'])
                identity='witness#'+hashlib.sha256(signature.encode()).hexdigest()
                result.update(id=identity,name=identity)
                ready[address]=result
                return result
            row = nodes.get(address, {})
            if row.get('dependency'):
                convert(row['dependency'])  # Preserve the leaf failure in coverage.
            if row.get('status') != 'captured':
                error=Refusal(row.get('reason', 'dependency was not captured'))
                if 'metadata_kind' in row:
                    error.metadata_kind=row['metadata_kind']
                raise error
            require(row.get('canonical') is True, 'noncanonical generic result')
            result = export(row.get('abi_family', 'generic-value'), row, address, reference, refs, slide)
            retained=retained_keys.get(bound_key(result))
            if retained is not None:
                ready[address]=retained
                return retained
            signature = key_identity(result['key'])
            identity = by_key.get(signature, 'metadata#' + hashlib.sha256(signature.encode()).hexdigest())
            by_key[signature] = identity
            result['id'] = identity
            ready[address] = result
            return result
        except (Refusal, KeyError) as e:
            error=e if isinstance(e,Refusal) else Refusal(str(e))
            blocked[address] = error
            raise error
        finally:
            visiting.discard(address)

    outcomes = []
    included = set()
    # ready only grows (each address is converted once), so the first row of each identity, in insertion order, is
    # what a scan of ready.values() would find. Index it incrementally: a full scan per owned reference was
    # quadratic and took hours on a 155k-constructor witness capture.
    owned_rows, indexed = {}, [0]
    def owned_row(identity):
        if identity not in owned_rows and indexed[0] < len(ready):
            for r in itertools.islice(ready.values(), indexed[0], None):
                owned_rows.setdefault(record_id(r), r)
            indexed[0] = len(ready)
        return owned_rows[identity]
    def include(row):
        identity = record_id(row)
        if identity in included or identity in merged:
            return
        included.add(identity)
        for ref in row['key']+[p['ref'] for p in row['pointers']]:
            if 'owned' in ref and ref['owned'] not in merged:
                include(owned_row(ref['owned']))
    for root in roots:
        try:
            require(not root.get('error'), root.get('error'))
            source = nodes.get(root['metadata'], {})
            if source.get('status') == 'compiler_static':
                require(source.get('canonical') is True and source.get('static_specialization') is True,
                        'compiler metadata is not a canonical static specialization')
                # Preserve section/UUID identities, not process addresses. This
                # records existing coverage without adding duplicate objects.
                metadata = refs.encode(root['metadata']-slide)
                descriptor = refs.encode(source['descriptor']-slide)
                outcomes.append(dict(name=root['name'], status='compiler_static',
                                     canonical_name=source['name'], metadata=metadata, descriptor=descriptor))
                continue
            row = convert(root['metadata'])
            include(row)
            outcomes.append(dict(name=root['name'], status='included', canonical_name=row['name'], record_id=record_id(row)))
        except Refusal as e:
            outcome=dict(name=root['name'], status='unsupported', reason=str(e))
            if hasattr(e,'metadata_kind'):
                outcome['metadata_kind']=e.metadata_kind
            outcomes.append(outcome)
    # Direct construction and ordinary protocol lookup can choose different
    # witnesses. Include the native-selected closure without replacing the
    # direct key or pretending an unsupported selected dependency was generated.
    ordinary_outcomes=[]
    pending=[row for row in events if 'conformance_lookup' in row]
    while pending:
        deferred=[]
        for lookup in pending:
            source=ready.get(lookup['conformance_lookup'])
            if source is None and lookup['conformance_lookup']-slide in installed:
                source=merged[installed[lookup['conformance_lookup']-slide]]
            if source is None or (record_id(source) not in included and record_id(source) not in merged):
                deferred.append(lookup)
                continue
            outcome=dict(source=record_id(source))
            try:
                require(lookup['selected'], 'ordinary conformance lookup returned no witness')
                selected=reference(lookup['selected'])
                if 'owned' in selected:
                    identity=selected['owned']
                    include(merged[identity] if identity in merged else ready[lookup['selected']])
                outcome.update(status='included' if 'owned' in selected else 'compiler_provided',
                               selected=selected)
            except Refusal as error:
                outcome.update(status='unsupported',reason=str(error))
                if hasattr(error,'metadata_kind'):outcome['metadata_kind']=error.metadata_kind
            ordinary_outcomes.append(outcome)
        if len(deferred)==len(pending):break
        pending=deferred
    # Section UUIDs are checked by materialize against the destination family;
    # a base catalog captured before a rebuild can still describe the same images.
    additions = {record_id(r): r for r in ready.values() if record_id(r) in included and record_id(r) not in merged}
    merged.update(additions)
    loads = sorted(set(base_catalog.get('loads', [])) | set(recipe.get('loads', [])))
    catalog = dict(schema=3, kind='swift-value-catalog', source_cache_uuid=header['cache_uuid'],
                   loads=loads, records=list(merged.values()))
    report = dict(requested=len(roots), included=sum(r['status']=='included' for r in outcomes),
                  compiler_provided=sum(r['status']=='compiler_static' for r in outcomes),
                  recovered_prefix=prefix,
                  added=len(additions), retained=len(merged)-len(additions), entries=outcomes)
    if ordinary_outcomes:
        report['ordinary_conformances']=ordinary_outcomes
    if witness_closures:
        # Keep both node kinds explicit so the producer installs separate maps
        # and supplies the matching native witness consumer.
        catalog['schema']=4
        catalog['kind']='swift-dependency-catalog'
        catalog['witnesses']=[r for r in catalog['records'] if r.get('record_kind')=='protocol-witness']
        catalog['records']=[r for r in catalog['records'] if r.get('record_kind')!='protocol-witness']
        report['added_witnesses']=sum(r.get('record_kind')=='protocol-witness' for r in additions.values())
        report['added_metadata']=len(additions)-report['added_witnesses']
        report['retained_witnesses']=len(catalog['witnesses'])-report['added_witnesses']
        report['retained_metadata']=len(catalog['records'])-report['added_metadata']
        report['installation']='Dependency catalog: build with --swift-runtime-toolchain; installed acceptance remains OPEN.'
    return catalog, report


def materialize_graph(catalog, family, address):
    result = _materialize_records(catalog['records'], [], family, address)
    records = [{k:v for k,v in row.items() if k not in ('kind','offset','size','address_point')}
               for row in result['records']]
    return (result['data'], result['pointers'], result['metadata_entries'],
            result['roots'], records, result['original'])


def materialize_dependencies(catalog, family, address):
    """Allocate both node kinds; installing their maps is a separate operation."""
    require(catalog.get('schema') == 4 and catalog.get('kind') == 'swift-dependency-catalog',
            'unknown Swift dependency catalog')
    return _materialize_records(catalog['records'], catalog['witnesses'], family, address)


def _materialize_records(metadata, witnesses, family, address):
    from .swift_metadata import References, pointer_entries
    refs = References(family)
    entries, roots = pointer_entries(family)
    original = {key:list(value) for key,value in entries.items()}
    # The source ABI is v1.4 (enforced by pointer_entries); it has no witness map.
    witness_entries, metatype_entries = {}, {}
    base = roots['base']
    pending = {record_id(r):r for r in metadata+witnesses}
    require(pending and len(pending)==len(metadata)+len(witnesses), 'empty or duplicate Swift catalog')
    witness_ids = {record_id(r) for r in witnesses}
    resolved, records, pointers, data = {}, [], [], bytearray()
    # Swift's MetadataCacheKey compares witness arguments by conformance
    # descriptor. Keep exact map keys, but give equivalent keys one identity.
    # Captured ABI layouts also describe retained records of that descriptor.
    layouts = {refs.decode(r['key'][0])-base:r['type_arguments'] for r in metadata
               if 'type_arguments' in r}
    witness_descriptors, canonical = {}, {}
    reader = None
    def witness_descriptor(offset):
        nonlocal reader
        if offset not in witness_descriptors:
            if reader is None:
                from .objc_emit import FamilyReader
                reader = FamilyReader(family)
            witness_descriptors[offset] = reader.ptr(base+offset)-base
        return witness_descriptors[offset]
    def runtime_key(key):
        count = layouts.get(key[0])
        if count is None:
            return None
        return key[:1+count]+tuple(witness_descriptor(p) for p in key[1+count:])
    for key, values in original.items():
        semantic = runtime_key(key)
        if semantic is not None and len(values)==1:
            canonical.setdefault(semantic, base+values[0])
    def decode(ref, start=0):
        if 'owned' in ref:
            return resolved[ref['owned']]
        if 'local' in ref:
            return address+start+ref['local']
        return refs.decode(ref)
    while pending:
        ready = [name for name,r in pending.items() if all(
            'owned' not in ref or ref['owned'] in resolved for ref in r['key']+[p['ref'] for p in r['pointers']])]
        require(ready, 'cyclic or missing Swift owned dependency')
        for name in ready:
            row = pending.pop(name)
            is_witness = name in witness_ids
            kind = 'protocol-witness' if is_witness else 'metadata'
            table = witness_entries if is_witness else metatype_entries if row.get('record_kind')=='metatype' else entries
            key = tuple(decode(ref)-base for ref in row['key'])
            if is_witness:
                require(len(key)==2 and row.get('record_kind')=='protocol-witness', 'invalid Swift witness identity')
            semantic = runtime_key(key) if not is_witness and row.get('record_kind')!='metatype' else None
            alias = key not in table and semantic is not None and semantic in canonical
            if alias:
                table[key] = [canonical[semantic]-base]
            if key in table:
                require(len(table[key]) == 1, 'ambiguous existing Swift metadata identity')
                resolved[name] = base+table[key][0]
                if is_witness:
                    witness_descriptors[resolved[name]-base] = key[0]
                elif semantic is not None:
                    canonical.setdefault(semantic, resolved[name])
                records.append(dict(id=name,name=row['name'],kind=kind,key=list(key),address=resolved[name],existing=True,
                                    **({'canonical_alias':True} if alias else {}),
                                    **({'abi_family':'metatype'} if row.get('record_kind')=='metatype' else {})))
                continue
            blob = bytes.fromhex(row['data'])
            point = row['metadata_offset']
            if is_witness:
                require(len(blob)%8==0 and point%8==0 and 0<=point<=len(blob)-8, 'invalid Swift witness allocation')
                descriptor_fields = [p for p in row['pointers'] if p['offset']==point]
                require(len(descriptor_fields)==1 and decode(descriptor_fields[0]['ref'])==base+key[0],
                        'Swift witness descriptor differs from lookup key')
            else:
                require(len(blob)%8==0 and point==16 and len(blob)>24, 'invalid Swift value allocation')
            # v2 slide chains support four-byte alignment but no pointer may
            # straddle a page. Packed enum programs can contain such fields.
            for padding in range(0,4096,8):
                if all((address+len(data)+padding+p['offset'])%4096 <= 4088 for p in row['pointers']):
                    break
            else:
                raise Refusal('Swift allocation has no page-safe pointer placement')
            data.extend(b'\0'*padding)
            start = len(data)
            data.extend(blob)
            for p in row['pointers']:
                require(p['offset']%4==0 and 0<=p['offset']<=len(blob)-8, 'Swift pointer field outside allocation')
                if 'local' in p['ref']:
                    require(0<=p['ref']['local']<len(blob), 'Swift local pointer outside allocation')
                pointers.append(dict(address=address+start+p['offset'],target=decode(p['ref'],start)))
            resolved[name] = address+start+point
            table[key] = [resolved[name]-base]
            if is_witness:
                witness_descriptors[resolved[name]-base] = key[0]
            elif semantic is not None:
                canonical.setdefault(semantic, resolved[name])
            records.append(dict(id=name,name=row['name'],kind=kind,key=list(key),address=resolved[name],existing=False,
                                offset=start,size=len(blob),address_point=point,
                                **({'abi_family':'metatype'} if row.get('record_kind')=='metatype' else {})))
    normalized, shapes = {}, {}
    for key, values in entries.items():
        types = layouts.get(key[0])
        if types is None or len(key)<=1+types:
            continue
        require(0<=types<len(key)-1<=63, 'unsupported normalized metadata key arity')
        shape = (types, len(key)-1)
        require(shapes.setdefault(key[0],shape)==shape, 'inconsistent metadata descriptor key arity')
        semantic = runtime_key(key)
        require(normalized.setdefault(semantic,values)==values, 'conflicting normalized metadata identities')
    return dict(data=bytes(data),pointers=pointers,metadata_entries=entries,witness_entries=witness_entries,metatype_entries=metatype_entries,
                normalized_entries=normalized,metadata_shapes=shapes,
                roots=roots,records=records,original=original)


def is_optional(name):
    """Recognize outer Optional in swift-inspect's type mangling, not display text.

    A function returning Optional ends in its function operator, not Sg; an
    Optional function ends in Sg. This also includes nested Optional wrappers.
    """
    name = name.removeprefix('$s')
    return name.endswith('Sg') or name.startswith('s8OptionalOy') and name.endswith('G')


def referenced_modules(display):
    return sorted(set(re.findall(r'(?<![\w.])([A-Za-z_]\w*)\.', display)))


def referenced_objc_names(display):
    return sorted(set(re.findall(r'(?<![\w.])__C\.([A-Za-z_]\w*)', display)))


def objc_owners(family, wanted):
    """Resolve imported ObjC classes to their defining images, not all of __C."""
    from .objc_emit import FamilyReader
    from .objc_transfer import sections
    from .cache import commands
    reader = FamilyReader(family)
    wanted_bytes = {name.encode():name for name in wanted}
    result = {}
    for path,header in family.images:
        for _,name,address,size in sections(commands(family,header)):
            if name != '__objc_classlist':
                continue
            for field in range(address,address+size,8):
                cls=reader.ptr(field)
                ro=reader.ptr(cls+32)&~7
                # Swift classes can use symbolic (binary) mangled names here.
                # Imported ObjC identifiers match the requested UTF-8 bytes.
                name=wanted_bytes.get(reader.string_bytes(reader.ptr(ro+24)))
                if name is not None:
                    result.setdefault(name,set()).add(path)
    return {name:sorted([p for p in paths if not p.startswith('/System/iOSSupport/')] or paths)
            for name,paths in sorted(result.items())}


def discover(mangled, demangled, owners, optionals_only=False, imported_owners=None, all_observed=False):
    """Join swift-inspect snapshots by metadata address, never remangle display names."""
    def names(doc):
        return {r['address']:r['name'] for block in doc for r in block.get('metadata',[])}
    display = names(demangled)
    rows, excluded, loads = {}, [], set()
    for address,name in names(mangled).items():
        if optionals_only and not is_optional(name):
            continue
        shown = display.get(address, '')
        # Optional itself belongs to Swift, including tuple/function/container
        # arguments and app-defined arguments. Do not silently omit those roots
        # because their display spelling does not start with a cache module.
        module = 'Swift' if optionals_only or all_observed else shown.split('.',1)[0]
        if optionals_only or all_observed:
            for dependency in referenced_modules(shown):
                if dependency != '__C' and dependency in owners:
                    paths = owners[dependency]
                    loads.update(paths if isinstance(paths,list) else [paths])
            for imported in referenced_objc_names(shown):
                loads.update((imported_owners or {}).get(imported,[]))
        if module not in owners:
            excluded.append(dict(name=name,display=shown,reason='owner module not selected'))
            continue
        # swift-inspect returns type manglings, occasionally with the symbol prefix.
        name = name.removeprefix('$s')
        paths=owners[module] if isinstance(owners[module],list) else [owners[module]]
        loads.update(paths)
        rows[name] = dict(name=name,owner=paths[0])
    require(rows, 'no types matched the selected framework owners')
    return dict(schema=1,kind='generic-values',owners=owners,loads=sorted(loads),
                records=[rows[n] for n in sorted(rows)]), excluded


def discover_main(argv):
    import argparse
    from pathlib import Path
    from .swift_metadata import provenance, write_json
    ap=argparse.ArgumentParser(description='Make an extensible recipe from paired swift-inspect snapshots')
    ap.add_argument('mangled',type=Path); ap.add_argument('demangled',type=Path)
    ap.add_argument('owners',type=Path,help='JSON map from Swift module name to framework image path')
    ap.add_argument('output',type=Path)
    mode=ap.add_mutually_exclusive_group()
    mode.add_argument('--all-observed',action='store_true',help='attempt every inventoried root, including app types and composite shapes')
    mode.add_argument('--optionals-only',action='store_true',help='capture every observed outer Optional and its dependencies')
    ap.add_argument('--cache',type=Path,help='matching cache for exact imported ObjC class owners')
    ap.add_argument('--type-processes',type=Path,help='inventory type-processes.json for per-process coverage counts')
    a=ap.parse_args(argv); a.output.mkdir(parents=True)
    mangled,display,owners=(json.loads(p.read_text()) for p in (a.mangled,a.demangled,a.owners))
    imported={}; cache_inputs=[]
    if a.cache:
        from .loader_generation import LoaderFamily
        family=LoaderFamily(a.cache)
        try:
            wanted={name for block in display for row in block.get('metadata',[])
                    for name in referenced_objc_names(row['name'])}
            imported=objc_owners(family,wanted)
            cache_inputs=[f.path for f in family.files]
        finally:
            family.close()
    recipe,excluded=discover(mangled,display,owners,a.optionals_only,imported,a.all_observed)
    shown={r['address']:r['name'] for block in display for r in block.get('metadata',[])}
    membership=json.loads(a.type_processes.read_text()) if a.type_processes else {}
    selected={r['name'] for r in recipe['records']}
    entries=[]
    for block in mangled:
        for row in block.get('metadata',[]):
            name=row['name'].removeprefix('$s')
            if a.optionals_only and not is_optional(name):
                continue
            text=shown.get(row['address'],'')
            modules=referenced_modules(text)
            entries.append(dict(name=name,display=text,selected=name in selected,
                                processes=membership.get(name,[]),
                                referenced_cache_modules=[m for m in modules if m in owners],
                                modules_without_cache_owner=[m for m in modules if m!='__C' and m not in owners],
                                imported_class_owners={n:imported[n] for n in referenced_objc_names(text) if n in imported},
                                unresolved_imported_names=[n for n in referenced_objc_names(text) if n not in imported]))
    entries.sort(key=lambda r:r['name'])
    processes={p:sum(p in r['processes'] for r in entries)
               for p in sorted({p for r in entries for p in r['processes']})}
    write_json(a.output/'selection.json',dict(scope='outer Optional' if a.optionals_only else 'all types',
        observed=len(entries),selected=len(selected),excluded=len(excluded),process_counts=processes,entries=entries))
    write_json(a.output/'recipe.json',recipe); write_json(a.output/'excluded.json',excluded)
    write_json(a.output/'build.json',provenance([a.mangled,a.demangled,a.owners]+
        ([a.type_processes] if a.type_processes else [])+cache_inputs,operation='swift-discovery',optionals_only=a.optionals_only,all_observed=a.all_observed))
    print(a.output/'recipe.json')
