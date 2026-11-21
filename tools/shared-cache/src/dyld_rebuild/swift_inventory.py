"""Itemize a Swift catalog: one row per entry, what it is, whether the shipped cache still holds
it, and which catalog, session and process first contributed it.

Identity is the catalog's own: `id` (a content hash since schema 3) or, for legacy rows, the
mangled name (swift_graph.record_id). Installed state is read from the cache's registered maps the
way swift_installed.records reads them, but every entry gets a row instead of the first miss
stopping the read. Provenance comes only from structured records: each catalog artifact's
build.json (its base catalogs), moves.json (inputs since moved out of scratch), and, for
expansions, graph.json (generated/retained facts and each request's source).
"""
import argparse
from bisect import bisect_right
from collections import Counter, defaultdict
import fnmatch
import json
import os
from pathlib import Path
import re
import struct
import subprocess

from .exports import Refusal
from .swift_graph import record_id
from .swift_metadata import ROOT, provenance, write_json

# The store this generator's inputs and outputs live in: SHARED_CACHE_STORE, else the repository's store/.
STORE = Path(os.environ.get('SHARED_CACHE_STORE', str(ROOT.parents[1] / 'store')))
TOP = STORE.parent
AREAS = {'artifacts': 'stored', 'inputs': 'stored', 'runs': 'stored', 'cache': 'stored', 'tmp': 'scratch'}
NOISE = {'inventory', 'reflection', 'swift', 'normalized', 'type-processes', 'graph', 'catalog'}
COLUMNS = ('id', 'kind', 'display', 'owner', 'images', 'providers', 'depends_on', 'dependents', 'status', 'reason',
           'adoption', 'address', 'normalized', 'associated_slots', 'associated_stale', 'first_catalog',
           'introduced_by', 'introduced_stored', 'how', 'sessions', 'sessions_stored', 'requested_by',
           'requested_in', 'bytes_from', 'id_form', 'name', 'index')


def moved(path, moves):
    """PATH after an artifact's moves.json (same rule as pipeline/graph.py): the first matching prefix;
    a target IMAGE!/ names a disk image that holds the path."""
    for old, new in moves.items():
        if not old.startswith('_') and path.startswith(old):
            return new.split('!/', 1)[0] if '!/' in new else new + path[len(old):]
    return path


def storage(path):
    """stored (store/artifacts|inputs|runs|cache), scratch (store/tmp), repo, external or missing."""
    p = Path(path)
    if not p.exists():
        return 'missing'
    try:
        area = p.resolve().relative_to(STORE.resolve()).parts[0]
        return AREAS.get(area, 'stored')
    except ValueError:
        return 'repo' if p.resolve().is_relative_to(TOP.resolve()) else 'external'


def session_label(path):
    """A short session name from an observation file's path: its folder under the store area, without
    the adopted-inputs artifact prefix and generic folder or file names."""
    parts = Path(path).with_suffix('').parts
    if 'store' in parts:
        parts = parts[parts.index('store') + 2:]
        if parts and '-inputs-' in parts[0]:
            parts = parts[1:]
    return '/'.join(p for p in parts if p not in NOISE) or str(path)


def entries(catalog):
    """(id, kind, row, index) for every record and witness, in catalog order."""
    for field in ('records', 'witnesses'):
        for index, row in enumerate(catalog.get(field, [])):
            kind = 'witness-table' if field == 'witnesses' else (
                'metatype' if row.get('record_kind') == 'metatype' else 'metadata')
            yield record_id(row), kind, row, index


def refs(row):
    return list(row['key']) + [p['ref'] for p in row['pointers']]


def owned(row):
    return sorted({r['owned'] for r in refs(row) if 'owned' in r})


def dependency_order(rows):
    """Ids with every owned dependency before its dependents (the materializer needs a DAG)."""
    order, state = [], {}
    for start in rows:
        stack = [(start, iter(owned(rows[start])))]
        if start in state:
            continue
        state[start] = 1
        while stack:
            node, children = stack[-1]
            child = next(children, None)
            if child is None:
                stack.pop()
                state[node] = 2
                order.append(node)
            elif child not in state and child in rows:
                state[child] = 1
                stack.append((child, iter(owned(rows[child]))))
    return order


# ---------------------------------------------------------------- provenance chain

class Link:
    """One catalog of the expansion chain, as its artifact records it."""
    def __init__(self, catalog):
        self.catalog = Path(catalog)
        self.artifact = self.catalog.parent
        self.name = self.artifact.name
        self.state = storage(self.catalog)
        record = self.artifact / 'build.json'
        self.build = json.loads(record.read_text()) if self.state != 'missing' and record.is_file() else None
        moves = self.artifact / 'moves.json'
        self.moves = json.loads(moves.read_text()) if moves.is_file() else {}
        self.graph = self.artifact / 'graph.json' if (self.artifact / 'graph.json').is_file() else None
        rows = self.build.get('inputs', []) if self.build else []
        rows = [r for r in rows if isinstance(r, dict) and isinstance(r.get('path'), str)]
        self.recorded = {moved(r['path'], self.moves): r.get('sha256') for r in rows}
        self.bases = sorted({p for p in self.recorded if p.endswith('/catalog.json') and Path(p).parent != self.artifact})
        self.inputs = list(self.recorded)
        self.recovered = {}

    def recoverable(self, base):
        """An expansion imports its base catalog into requests.json; exporting it again with the
        writer's own serialization must reproduce the recorded sha256 of the base."""
        requests = self.artifact / 'requests.json'
        if not requests.is_file() or not self.recorded.get(base):
            return None
        if base not in self.recovered:
            import hashlib
            from .metadata_graph import MetadataGraph
            exported = MetadataGraph(json.loads(requests.read_text())).catalog()
            wire = (json.dumps(exported, indent=2, sort_keys=True) + '\n').encode()
            self.recovered[base] = hashlib.sha256(wire).hexdigest() == self.recorded[base]
        return self.recovered[base]

    def describe(self, ids=None):
        b = self.build or {}
        return dict(name=self.name, catalog=str(self.catalog), state=self.state, operation=b.get('operation'),
                    revision=b.get('revision'), date=b.get('date'), git_commit=b.get('git_commit'),
                    cache_uuid=b.get('cache_uuid'), entries=None if ids is None else len(ids),
                    bases=[dict(name=Path(p).parent.name, state=storage(p), sha256=self.recorded.get(p),
                                **({'recovered_from_requests_json': self.recoverable(p)}
                                   if storage(p) == 'missing' and self.state != 'missing' else {}))
                           for p in self.bases],
                    graph=self.graph is not None,
                    missing_inputs=sorted({str(Path(p).parent) for p in self.inputs if storage(p) == 'missing'}))


def chain(catalog, history=()):
    """Every catalog reachable through build.json base-catalog inputs, plus HISTORY: stored catalogs
    the records no longer link because a link between them was deleted."""
    links, todo = {}, [str(Path(catalog).resolve())] + [str(Path(h).resolve()) for h in history]
    while todo:
        path = todo.pop()
        if path in links:
            continue
        links[path] = Link(path)
        todo.extend(links[path].bases)
    return list(links.values())


def inventory_names(path):
    from .metadata_inventory import names
    return set(names(json.loads(Path(path).read_text())))


class Sessions:
    """Interned session and process labels, and how one expansion's request sources map to them."""
    def __init__(self):
        self.labels, self.index, self.stored = [], {}, {}

    def intern(self, label, state=None):
        if label not in self.index:
            self.index[label] = len(self.labels)
            self.labels.append(label)
        if state is not None:
            self.stored[label] = state
        return self.index[label]


def graph_attribution(link, graph, sessions, processes, read):
    """{object id: (session ids, process ids)} for this expansion's requests, and its object facts.
    READ collects every observation file opened."""
    evidence = graph.get('evidence', {})
    inventories = []
    for row in evidence.get('frontend', {}).get('sources', []):
        path = moved(row['path'], link.moves)
        state = storage(path)
        label = sessions.intern(session_label(path), state)
        inventories.append((label, inventory_names(path) if state != 'missing' else set()))
        if state != 'missing':
            read.add(Path(path))
    reflections = {}
    for row in evidence.get('frontends', []):
        if 'sha256' in row and 'path' in row:
            path = moved(row['path'], link.moves)
            state = storage(path)
            main = None
            if state != 'missing':
                images = json.loads(Path(path).read_text()).get('images') or []
                main = images[0].get('path') if images else None   # dyld lists the main executable first
                read.add(Path(path))
            reflections[row['sha256']] = (sessions.intern(session_label(path), state), main)
    components = {}
    if (link.artifact / 'components.json').is_file():
        from .component_metadata import component_identity
        for c in json.loads((link.artifact / 'components.json').read_text())['components']:
            components[component_identity(c)] = c['path']
    direct = defaultdict(lambda: (set(), set()))
    for request in graph['requests']:
        target = request.get('object')
        if target is None:
            continue
        found, procs = direct[target]
        source = request.get('source')
        name = request['name'].removeprefix('$s')
        if isinstance(source, list):
            procs.update(processes.intern(p) for p in source)
            found.update(label for label, names in inventories if name in names)
        elif isinstance(source, dict) and source.get('input') in reflections:
            label, main = reflections[source['input']]
            found.add(label)
            if main:
                procs.add(processes.intern(main))
        elif isinstance(source, dict) and source.get('input') in components:
            path = components[source['input']]
            found.add(sessions.intern('static:' + path, 'stored'))
        else:
            found.add(sessions.intern('unrecorded source', 'missing'))
    facts = {key: node['facts'][0] for key, node in graph['objects'].items()}
    return direct, facts


def propagate(order, rows, direct):
    """Give each owned dependency the requesters of every entry that reaches it."""
    acc = {i: (set(direct[i][0]), set(direct[i][1])) if i in direct else (set(), set()) for i in rows}
    for node in reversed(order):            # dependents before their dependencies
        s, p = acc[node]
        if s or p:
            for child in owned(rows[node]):
                if child in acc:
                    acc[child][0].update(s)
                    acc[child][1].update(p)
    return acc


def provenance_rows(catalog_path, catalog, rows, order, history=()):
    """Origin columns for every entry, and the chain's description."""
    links = chain(catalog_path, history)
    stored = [l for l in links if l.state != 'missing']
    stored.sort(key=lambda l: (l.build or {}).get('date') or '')
    sessions, processes = Sessions(), Sessions()
    contents, described = {}, []
    shipped = {i: content_hash(row) for i, row in rows.items()}
    first, bytes_from, generation, requested = {}, {}, {}, defaultdict(lambda: (set(), set()))
    read = set()
    for link in stored:
        if link.catalog.resolve() == Path(catalog_path).resolve():
            current = catalog
        else:
            current = json.loads(link.catalog.read_text())
        ids = {}
        for i, _, row, _ in entries(current):
            ids[i] = content_hash(row) if i in rows else None
        del current
        for i, h in ids.items():
            if i in rows:
                first.setdefault(i, link)
                if h == shipped[i]:
                    bytes_from.setdefault(i, link)
        described.append(link.describe(ids))
        if any(storage(b) == 'missing' for b in link.bases) and (link.artifact / 'requests.json').is_file():
            read.add(link.artifact / 'requests.json')
        if link.graph is None:
            continue
        graph = json.loads(link.graph.read_text())
        direct, facts = graph_attribution(link, graph, sessions, processes, read)
        del graph
        acc = propagate(order, rows, direct)
        for i, (s, p) in acc.items():
            requested[i][0].update(s)
            requested[i][1].update(p)
            if first.get(i) is link and facts.get(i) == 'generated':
                generation[i] = (s, p)
        contents[link.name] = facts
    for link in links:
        if link.state == 'missing':
            described.append(link.describe())
    result = {}
    for i in rows:
        link = first.get(i)
        if link is None:
            result[i] = dict(first_catalog='', introduced_by='', introduced_stored='', how='not in any chain catalog')
            continue
        facts = contents.get(link.name)
        if facts is not None and facts.get(i) == 'generated':
            by, how = [link], 'generated'
            s = generation.get(i, (set(), set()))[0]
        else:
            missing = [p for p in link.bases if storage(p) == 'missing']
            by = [Path(p).parent.name for p in missing] or [link.name]
            how = ('retained from a missing base' if facts is not None else
                   (link.build or {}).get('operation') or 'unrecorded')
            s = set()
        names = [b if isinstance(b, str) else b.name for b in by]
        labels = sorted(sessions.labels[k] for k in s)
        states = {sessions.stored.get(l, 'missing') for l in labels}
        stored_by = ('yes' if all(n == link.name for n in names) else
                     'recoverable' if all(link.recovered.get(p) for p in link.bases if storage(p) == 'missing')
                     else 'no')
        result[i] = dict(
            first_catalog=link.name, introduced_by='+'.join(names), introduced_stored=stored_by, how=how,
            sessions=';'.join(labels),
            sessions_stored='' if not labels else 'yes' if states <= {'stored', 'repo'} else
                            'no' if not states & {'stored', 'repo'} else 'partial',
            requested_by=';'.join(sorted({os.path.basename(processes.labels[k]) for k in requested[i][1]})),
            requested_in=';'.join(sorted(sessions.labels[k] for k in requested[i][0])),
            bytes_from=bytes_from[i].name if i in bytes_from else '')
    return result, described, read


def content_hash(row):
    import hashlib
    wire = json.dumps([row['data'], row['key'], row['pointers'], row['metadata_offset']], sort_keys=True)
    return hashlib.sha256(wire.encode()).hexdigest()


# ---------------------------------------------------------------- installed state

class Installed:
    """What the shipped cache exposes: section identities, registered maps, bytes and pointers."""
    def __init__(self, family):
        from .objc_emit import FamilyReader
        from .swift_installed import auxiliary_entries
        from .swift_metadata import References, pointer_entries
        self.family = family
        self.refs = References(family)
        self.metadata, roots = pointer_entries(family)
        self.base = roots['base']
        self.aux = auxiliary_entries(family, roots)
        self.reader = FamilyReader(family)

    def decode(self, ref):
        return self.refs.decode(ref)

    def in_image(self, address):
        i = bisect_right(self.refs.starts, address) - 1
        return i >= 0 and self.refs.ranges[i][0] <= address < self.refs.ranges[i][1]

    def read(self, address, size):
        return self.reader.read(address, size)

    def ptr(self, address):
        return self.reader.ptr(address)

    def associated(self, witnesses):
        """Per-witness associated slot counts and stale slots, from the existing installed audit."""
        from functools import lru_cache
        from .witness_initializers import audit_installed, initializers
        audit = audit_installed(self.family, witnesses)
        slots = lru_cache(maxsize=None)(lambda d: len(initializers(self.reader, d)))
        counts = {w['id']: slots(self.reader.ptr(w['address'])) for w in witnesses}
        stale = Counter(i for row in audit['stale'] for i in row['records'])
        return counts, stale, {k: v for k, v in audit.items() if k != 'stale'}

    def names(self, descriptor):
        """(conforming type, protocol) of a conformance descriptor (Swift ABI Metadata.h,
        TargetProtocolConformanceDescriptor): protocol at +0 (indirectable), type reference at +4
        whose kind is ConformanceFlags bits 3-5."""
        r = self.reader
        def rel(address, indirect=False):
            offset = struct.unpack('<i', r.read(address, 4))[0]
            if not offset:
                return 0
            target = address + (offset & ~1 if indirect else offset)
            return r.ptr(target) if indirect and offset & 1 else target
        def context(address):
            parts = []
            for _ in range(12):
                if not address:
                    break
                kind = struct.unpack('<I', r.read(address, 4))[0] & 0x1f
                if kind in (0, 3, 16, 17, 18):      # module, protocol, class, struct, enum
                    parts.append(r.string(rel(address + 8)))
                if kind == 0:
                    break
                address = rel(address + 4, True)
            return '.'.join(reversed(parts))
        flags = struct.unpack('<I', r.read(descriptor + 12, 4))[0]
        kind = (flags >> 3) & 7
        target = rel(descriptor + 4)
        if kind == 1:
            target = r.ptr(target)
        conforming = (context(target) if kind in (0, 1) else
                      'ObjC.' + r.string(target) if kind == 2 else 'ObjC class')
        return conforming, context(rel(descriptor, True))


def stale_reason(error, ref):
    text = str(error)
    if isinstance(error, KeyError):
        return 'image or section absent: %s %s,%s' % (ref['image'], ref['segment'], ref['section'])
    return text


def readback(catalog, view, order=None):
    """{id: state} for every entry against VIEW; never stops at the first miss."""
    rows = {i: (kind, row) for i, kind, row, _ in entries(catalog)}
    order = order or dependency_order({i: r for i, (_, r) in rows.items()})
    base, state = view.base, {}
    layouts = {}
    for i, (kind, row) in rows.items():
        if kind == 'metadata' and 'type_arguments' in row:
            try:
                layouts[view.decode(row['key'][0]) - base] = row['type_arguments']
            except (Refusal, KeyError):
                pass
    for i in order:
        kind, row = rows[i]
        out = dict(status='current', reason='', adoption='', address=None, normalized='')
        state[i] = out
        decoded, problem = [], None
        for ref in row['key']:
            if 'owned' in ref:
                dep = state.get(ref['owned'])
                if dep is None or dep['address'] is None:
                    problem = ('stale' if dep and dep['status'] == 'stale' else 'unknown',
                               'depends on %s, which did not resolve' % ref['owned'])
                    break
                decoded.append(dep['address'])
            else:
                try:
                    decoded.append(view.decode(ref))
                except (Refusal, KeyError) as error:
                    problem = ('stale', stale_reason(error, ref))
                    break
        if problem is None:
            for p in row['pointers']:
                ref = p['ref']
                if 'owned' not in ref and 'local' not in ref:
                    try:
                        view.decode(ref)
                    except (Refusal, KeyError) as error:
                        problem = ('stale', stale_reason(error, ref))
                        break
        if problem:
            out.update(status=problem[0], reason=problem[1])
            continue
        key = tuple(a - base for a in decoded)
        table = view.metadata if kind == 'metadata' else view.aux
        values = table.get(key) or ()
        if len(values) != 1:
            out.update(status='unknown', reason='key absent from the installed %s map' % (
                'metadata' if kind == 'metadata' else 'witness/metatype') if not values else 'ambiguous installed key')
            continue
        address = base + next(iter(values))
        out['address'] = address
        if kind == 'metadata' and layouts.get(key[0]) is not None and len(key) > 1 + layouts[key[0]]:
            t = layouts[key[0]]
            semantic = key[:1 + t] + tuple(view.ptr(base + k) - base for k in key[1 + t:])
            out['normalized'] = 'yes' if semantic in view.aux else 'no'
        if view.in_image(address):
            out['adoption'] = 'existing'
            continue
        start = address - row['metadata_offset']
        data = bytearray.fromhex(row['data'])
        installed = bytearray(view.read(start, len(data)))
        match = True
        for p in row['pointers']:
            ref, at = p['ref'], p['offset']
            data[at:at + 8] = installed[at:at + 8] = bytes(8)
            target = (state[ref['owned']]['address'] if 'owned' in ref else
                      start + ref['local'] if 'local' in ref else view.decode(ref))
            match = match and view.ptr(start + at) == target
        out['adoption'] = 'generated' if match and data == installed else 'differs'
    by_address = defaultdict(list)
    for i, out in state.items():
        if out['adoption'] == 'generated':
            by_address[out['address']].append(i)
    for i, out in state.items():
        if out['adoption'] == 'differs':
            owners = by_address.get(out['address'])
            if owners:
                out.update(adoption='alias', reason='shares the allocation of %s' % owners[0])
            else:
                out.update(status='unknown', reason='installed bytes or relocations differ from the catalog')
    return state


# ---------------------------------------------------------------- report

def demangle(names):
    """Display spelling of each mangled name, from the toolchain's swift-demangle."""
    wanted = [n for n in names if n and not re.match(r'^(metadata|witness|metatype)#', n)]
    if not wanted:
        return {}
    from .artifacts import helper
    binary = helper('swift-demangle')
    command = [str(binary)] if binary is not None else ['xcrun', 'swift-demangle', '--simplified', '--compact']
    out = subprocess.run(command,
                         input=''.join('$s' + n.removeprefix('$s') + '\n' for n in wanted),
                         capture_output=True, text=True, check=True).stdout.splitlines()
    return {n: d for n, d in zip(wanted, out) if d != '$s' + n.removeprefix('$s')}


def build_rows(catalog, view, state, origins, demangled, associated, providers=()):
    rows = {i: r for i, _, r, _ in entries(catalog)}
    dependents = Counter(d for r in rows.values() for d in owned(r))
    providers = set(providers)
    display = {}
    out = []
    for i, kind, row, index in entries(catalog):
        if kind != 'witness-table':
            display[i] = demangled.get(row['name'], row['name'])
    for i, kind, row, index in entries(catalog):
        s, o = state[i], origins[i]
        images = sorted({r['image'] for r in refs(row) if 'image' in r})
        owner = row.get('owner') or row['key'][0].get('image', '')
        text = display.get(i, '')
        if kind == 'witness-table':
            try:
                conforming, protocol = view.names(view.decode(row['key'][0]))
            except (Refusal, KeyError, struct.error, ValueError):
                conforming, protocol = '?', '?'
            arg = row['key'][1] if len(row['key']) > 1 else {}
            text = '%s: %s' % (display.get(arg.get('owned'), conforming), protocol)
        slots, stale = associated
        out.append(dict(
            id=i, kind=kind, display=text, owner=owner, images=';'.join(x for x in images if x != owner),
            providers=';'.join(x for x in images if x in providers),
            depends_on=';'.join(owned(row)), dependents=dependents.get(i, 0), status=s['status'], reason=s['reason'],
            adoption=s['adoption'], address='%#x' % s['address'] if s['address'] else '', normalized=s['normalized'],
            associated_slots=slots.get(i, '') if kind == 'witness-table' else '',
            associated_stale=stale.get(i, 0) if kind == 'witness-table' else '',
            first_catalog=o['first_catalog'], introduced_by=o['introduced_by'],
            introduced_stored=o['introduced_stored'], how=o['how'], sessions=o.get('sessions', ''),
            sessions_stored=o.get('sessions_stored', ''), requested_by=o.get('requested_by', ''),
            requested_in=o.get('requested_in', ''),
            bytes_from=o.get('bytes_from', ''), id_form='content' if 'id' in row else 'name',
            name=row['name'], index=index))
    return out


def summarize(rows, described, audit, catalog_path, cache_path):
    """Counts; groupings are lists, largest first (JSON objects would be re-sorted by key)."""
    count = lambda field: [[k, n] for k, n in Counter(r[field] for r in rows).most_common()]
    def by(field, split=False):
        result = defaultdict(Counter)
        for r in rows:
            for value in (r[field].split(';') if split else [r[field]]):
                if value or not split:
                    result[value]['entries'] += 1
                    result[value][r['kind']] += 1
                    result[value]['status:' + r['status']] += 1
        return [dict(name=k, **v) for k, v in sorted(result.items(), key=lambda kv: (-kv[1]['entries'], kv[0]))]
    reasons = Counter(re.sub(r'#[0-9a-f]{64}', '#...', r['reason']) for r in rows if r['reason'])
    return dict(
        schema=1, kind='swift-catalog-inventory', catalog=str(catalog_path), cache=str(cache_path), entries=len(rows),
        kinds=count('kind'), status=count('status'), adoption=count('adoption'), normalized=count('normalized'),
        reasons=[[k, n] for k, n in reasons.most_common(50)], id_form=count('id_form'),
        associated_witnesses=audit, how=count('how'), introduced_by=by('introduced_by'),
        introduced_stored=count('introduced_stored'), first_catalog=by('first_catalog'),
        bytes_from=count('bytes_from'), sessions=by('sessions', True), sessions_stored=count('sessions_stored'),
        requested_in=by('requested_in', True), requested_by=by('requested_by', True),
        with_requesters=sum(bool(r['requested_by'] or r['requested_in']) for r in rows),
        owners=by('owner'), images=by('images', True), providers=by('providers', True), chain=described,
        scope='Installed state is the shipped cache read back offline; native adoption and app use are not measured. '
              'Origins are the earliest stored catalog containing each id and, for expansions, the request '
              'sources reaching it through owned dependencies.')


def write_tsv(path, rows):
    clean = lambda v: str(v).replace('\t', ' ').replace('\n', ' ')
    with open(path, 'w') as out:
        out.write('\t'.join(COLUMNS) + '\n')
        for r in rows:
            out.write('\t'.join(clean(r[c]) for c in COLUMNS) + '\n')


def read_tsv(path):
    with open(path) as stream:
        header = stream.readline().rstrip('\n').split('\t')
        return [dict(zip(header, line.rstrip('\n').split('\t'))) for line in stream]


def inventory(catalog_path, cache_path, output, history=()):
    from .loader_generation import LoaderFamily
    output.mkdir(parents=True, exist_ok=False)
    catalog = json.loads(catalog_path.read_text())
    rows = {i: r for i, _, r, _ in entries(catalog)}
    order = dependency_order(rows)
    origins, described, read = provenance_rows(catalog_path, catalog, rows, order, history)
    record = cache_path.parent / 'build.json'
    providers = json.loads(record.read_text()).get('providers', {}).values() if record.is_file() else ()
    family = LoaderFamily(cache_path)
    try:
        view = Installed(family)
        witnesses = []
        state = readback(catalog, view, order)
        for i, kind, row, _ in entries(catalog):
            if kind == 'witness-table' and state[i]['address']:
                witnesses.append(dict(id=i, address=state[i]['address']))
        slots, stale, audit = view.associated(witnesses)
        names = [r['name'] for r in catalog['records']]
        result = build_rows(catalog, view, state, origins, demangle(names), (slots, stale), providers)
        cache_files = [f.path for f in family.files]
    finally:
        family.close()
    write_tsv(output / 'inventory.tsv', result)
    write_json(output / 'summary.json', summarize(result, described, audit, catalog_path, cache_path))
    inputs = [catalog_path, *map(Path, cache_files)] + ([record] if record.is_file() else [])
    for link in chain(catalog_path, history):
        for p in (link.catalog, link.artifact / 'build.json', link.artifact / 'moves.json', link.graph,
                  link.artifact / 'components.json'):
            if p and Path(p).is_file() and Path(p) not in inputs:
                inputs.append(Path(p))
    inputs += sorted(read - set(inputs))
    write_json(output / 'build.json', provenance(inputs, operation='swift-inventory',
               history=[str(h) for h in history]))
    return output


# ---------------------------------------------------------------- selection

FIELDS = {'id': 'id', 'kind': 'kind', 'owner': 'owner', 'image': 'images', 'introduced_by': 'introduced_by',
          'first_catalog': 'first_catalog', 'session': 'sessions', 'requested_by': 'requested_by',
          'status': 'status'}


def matches(rule, row):
    """A rule matches when every field matches (fnmatch); list columns match on any element."""
    for field, pattern in rule.items():
        column = FIELDS.get(field)
        if column is None:
            raise Refusal('unknown selection field: ' + field)
        values = row[column].split(';') if column in ('images', 'sessions', 'requested_by') else [row[column]]
        if field == 'image':
            values.append(row['owner'])
        patterns = pattern if isinstance(pattern, list) else [pattern]
        if not any(fnmatch.fnmatchcase(v, p) for v in values for p in patterns):
            return False
    return True


def select(catalog, rows, selection):
    """Entries the selection names, plus the owned closure the materializer requires."""
    include, exclude = selection.get('include'), selection.get('exclude', [])
    chosen = {r['id'] for r in rows if (include is None or any(matches(x, r) for x in include))
              and not any(matches(x, r) for x in exclude)}
    by_id = {i: r for i, _, r, _ in entries(catalog)}
    closure, added, todo = set(chosen), {}, list(chosen)
    while todo:
        node = todo.pop()
        for dep in owned(by_id[node]):
            if dep not in closure:
                closure.add(dep)
                added[dep] = node
                todo.append(dep)
    result = dict(catalog, records=[r for r in catalog['records'] if record_id(r) in closure],
                  witnesses=[r for r in catalog.get('witnesses', []) if record_id(r) in closure])
    kinds = {r['id']: r['kind'] for r in rows}
    full_layouts = {json.dumps(r['key'][0], sort_keys=True) for r in catalog['records'] if 'type_arguments' in r}
    kept_layouts = {json.dumps(r['key'][0], sort_keys=True) for r in result['records'] if 'type_arguments' in r}
    report = dict(schema=1, kind='swift-catalog-selection', selection=selection, entries=len(rows),
                  selected=len(chosen), closure_added=len(added), kept=len(closure), dropped=len(rows) - len(closure),
                  kept_by_kind=dict(Counter(kinds[i] for i in closure)),
                  closure_by_kind=dict(Counter(kinds[i] for i in added)),
                  closure=[dict(id=i, kind=kinds[i], required_by=added[i]) for i in sorted(added)],
                  normalized_layouts_lost=len(full_layouts - kept_layouts),
                  scope='Owned references (keys and payload pointers) are the closure the cache materializer '
                        'requires; associated witness slots point to image initializers, not entries.')
    return result, report


def select_main(args):
    catalog = json.loads(args.catalog.read_text())
    rows = read_tsv(args.inventory)
    selection = json.loads(args.selection.read_text())
    result, report = select(catalog, rows, selection)
    args.output.mkdir(parents=True, exist_ok=False)
    write_json(args.output / 'catalog.json', result)
    write_json(args.output / 'selection-report.json', report)
    write_json(args.output / 'build.json', provenance([args.catalog, args.inventory, args.selection],
               operation='swift-select'))
    print(json.dumps({k: v for k, v in report.items() if k != 'closure'}, indent=1))
    return 0


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('catalog', type=Path, help='catalog.json the cache was built from')
    parser.add_argument('cache', type=Path, help='main file of the cache family built from it')
    parser.add_argument('output', type=Path, help='new folder: inventory.tsv, summary.json, build.json')
    parser.add_argument('--history', type=Path, action='append', default=[],
                        help='stored catalog.json the build.json chain no longer reaches (a deleted link); repeat')
    args = parser.parse_args(argv)
    out = inventory(args.catalog.resolve(), args.cache.resolve(), args.output, args.history)
    summary = json.loads((out / 'summary.json').read_text())
    print(json.dumps({k: summary[k] for k in ('entries', 'kinds', 'status', 'adoption', 'how')}, indent=1))
    return 0


def select_command(argv):
    parser = argparse.ArgumentParser(description='Write the subset of a catalog a selection names, with its closure')
    parser.add_argument('catalog', type=Path)
    parser.add_argument('inventory', type=Path, help='inventory.tsv of that catalog (make inventory)')
    parser.add_argument('selection', type=Path, help='JSON: {"include": [RULE...], "exclude": [RULE...]}')
    parser.add_argument('output', type=Path)
    return select_main(parser.parse_args(argv))
