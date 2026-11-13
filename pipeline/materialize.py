#!/usr/bin/python3 -I
"""materialize: make each declared file once, into pack overlays that hao patch and the installer apply.

  materialize.py SET TARGET BUILD --out DIR [--store DIR] [--system ROOT] [--packs auto|A,B] [--only PATH]... [--json]

The ledger (pipeline/ledger.py) gives every declared path its base and its writers in set order. Each path is
composed once: the base bytes when the first writer edits (OCLP's payload through its install map, Golden
Gate's system at --system, or a Tahoe donor payload), then each writer in turn: a whole file from its input row,
or an edit spec applied by tools/edit-engine (through --store, so an unchanged spec and input are reused by hash),
and an edited file is signed ad hoc once, after its last edit (macOS codesign), with the entitlements of the file
the spec's "entitlements_from" names when it has one. A file inside a declared tree that has its own declaration
(the same recipe edits it, e.g. hao-opencore's stamped HaoExec) is made by that declaration, not by the tree.

The result goes to DIR/<pack>/files/<path>, and DIR/<pack>/manifest.json records sha256, mode, owner and flags
(the format tools/hao reads). Install attributes come from the declaration's "install" first; a file taken from a
tar member (source "ARCHIVE!MEMBER") otherwise keeps the member's mode and numeric owner, as the hooks' `tar -xp
--numeric-owner` does, and the manifest's "dirs" carries the tar's own folders above it (mode and owner) for the
folders hao has to create; anything else is root:wheel, 0755 for a Mach-O and 0644 otherwise, with no flags.

A path is "deferred" when it can only be made on the target (kc.link, kc.bind, cryptex.build, hao.embed-stub,
hao.stamp, files the hook generates) and "unresolved" when its source isn't found; both are reported with the reason, never
guessed. A deferred path also lists what it comes after on the target: the installed files its external op reads
(hao.stamp, kc.bind: the linked BootKC) and the declaration's "after" ("overlays": every overlay file installed first, e.g.
usr/lib/hao/native-identities, which is keyed on the CDHashes of the files as installed). DIR/report.json lists every
path's outcome; DIR/deferred.json lists the deferred paths in an order that satisfies those dependencies. A made
path's report entry also names its writers in set order, the files it made ("paths") and, as "base", the target
file it starts from or replaces ({path, sha256, edited}), so an image build installing the overlays
(pipeline/overlays.py, golden-run --from-overlays) can check it finds that file. A file from a tar member keeps the
member's "mtime" in the manifest unless its declaration gives install attributes.

Signatures: the materialiser signs with the host's codesign, which reserves no extra space after the blobs; the
builder VM's codesign pads each slice's signature with about 18 KB of zeros. Such a file differs from the image's in
its CDHash, size and LC_CODE_SIGNATURE/__LINKEDIT sizes only (tools/edit-engine/src/signing.py compare() reports
it as a note, not a problem); anything keyed on CDHashes must therefore be generated from the installed files.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import runpy
import sys
import tarfile

TOP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOP / 'tools' / 'edit-engine' / 'src'))
import engine as ENGINE     # noqa: E402
import signing as SIGNING   # noqa: E402
import spec as SPEC         # noqa: E402
import store as STORE       # noqa: E402

MACHO = (b'\xcf\xfa\xed\xfe', b'\xca\xfe\xba\xbe', b'\xca\xfe\xba\xbf')
OVERLAYS = 'overlays'      # an "after" entry: every overlay file of the build installed first


class Unresolved(Exception):
    pass


class Deferred(Exception):
    def __init__(self, message, after=()):
        super().__init__(message)
        self.after = list(after)


class Sources:
    """Where base files and input rows come from. Built from the repository's rows, or given directly in tests."""

    def __init__(self, rows=None, oclp_payload=None, oclp_map=None, system=None, tahoe_roots=()):
        self.rows = rows or {}                     # row -> path (file or folder)
        self.oclp_payload = oclp_payload           # OCLP hardware artifact's payload folder
        self.oclp_map = oclp_map or []             # [(destination, operation, support_package_relative_path)]
        self.system = system                       # Golden Gate's own system root
        self.tahoe_roots = list(tahoe_roots)       # donor payload roots, each rooted at /
        self._tars = {}                            # archive path -> {member name: TarInfo}
        self._open = {}                            # archive path -> open TarFile, read through once

    @classmethod
    def from_repo(cls, assembly_rows, system=None):
        art = assembly_rows.get('oclp_hardware')
        oclp_map = []
        if art and (Path(art) / 'install-map.json').is_file():
            m = json.loads((Path(art) / 'install-map.json').read_text())
            oclp_map = [(e['destination'].lstrip('/'), e['operation'], e['support_package_relative_path'])
                        for e in m.get('entries', [])]
        tahoe = []
        for row, path in sorted(assembly_rows.items()):
            if row.startswith('tahoe_') and row.endswith('_donors') and Path(path, 'payload').is_dir():
                tahoe += [str(p) for p in sorted(Path(path, 'payload').iterdir()) if p.is_dir()]
        return cls(assembly_rows, str(Path(art) / 'payload') if art else None, oclp_map, system, tahoe)

    def base(self, path, kind):
        if kind == 'oclp':
            if path.startswith('EFI/'):
                raise Deferred('the EFI partition is built on the target (OpenCore config from build-efi)')
            hit = None
            for dest, op, rel in self.oclp_map:
                if path == dest or path.startswith(dest.rstrip('/') + '/'):
                    hit = Path(self.oclp_payload or '', rel, os.path.relpath(path, dest) if path != dest else '')
            if hit and hit.is_file():
                return hit.read_bytes()
            raise Unresolved('OCLP payload has no file for %s' % path)
        if kind == 'apple':
            if not self.system:
                raise Unresolved('no --system root for Golden Gate files')
            p = Path(self.system, path)
            if p.is_file():
                return p.read_bytes()
            raise Unresolved('%s is not in the Golden Gate system at %s' % (path, self.system))
        if kind == 'tahoe':
            for root in self.tahoe_roots:
                if Path(root, path).is_file():
                    return Path(root, path).read_bytes()
            raise Unresolved('no Tahoe donor payload has %s' % path)
        raise Unresolved('base kind %s' % kind)

    def entitlements_source(self, src):
        """The bytes of the signed file a sign spec's entitlements_from names (the layer's file, or a row's)."""
        if src.get('kind') == 'hao':
            return self.row_file(src['row'], src.get('source') or '', src.get('source'))
        return self.base(src['path'], src['kind'])

    def _tar(self, archive):
        """The open TarFile of ARCHIVE, indexed once (a harvest tar holds thousands of members)."""
        key = str(archive)
        if key not in self._open:
            t = self._open[key] = tarfile.open(archive)
            self._tars[key] = {m.name[2:] if m.name.startswith('./') else m.name: m for m in t.getmembers()}
        return self._open[key]

    def close(self):
        for t in self._open.values():
            t.close()
        self._open, self._tars = {}, {}

    def _members(self, archive):
        self._tar(archive)
        return self._tars[str(archive)]

    def row_file(self, row, path, source=None):
        """The bytes a whole-file origin installs: SOURCE inside the row when declared, the row itself when it is a
        file, else PATH inside it."""
        src = self.rows.get(row)
        if not src:
            raise Unresolved('row %s is not resolved' % row)
        src = Path(src)
        if source:
            archive, _, member = source.partition('!')
            if member:
                try:
                    t = self._tar(src / archive)
                    info = self._members(src / archive).get(member)
                    f = t.extractfile(info) if info is not None else None
                    if f is None:
                        raise KeyError(member)
                    return f.read()
                except (OSError, KeyError, tarfile.TarError) as x:
                    raise Unresolved('row %s: no %s in %s (%s)' % (row, member, archive, x))
            if (src / source).is_file():
                return (src / source).read_bytes()
            raise Unresolved('row %s has no %s' % (row, source))
        if src.is_file():
            return src.read_bytes()
        for cand in (src / path, src / 'root' / path, src / 'payload' / path, src / 'replace' / path):
            if cand.is_file():
                return cand.read_bytes()
        raise Unresolved('row %s (a folder) has no %s; declare "source" for it' % (row, path))

    def member_attrs(self, row, source):
        """(attributes, folders) of a tar member SOURCE ("ARCHIVE!MEMBER") of ROW, as `tar -xp --numeric-owner`
        installs it: {'mode', 'owner': 'UID:GID', 'mtime'} (the member's modification time, which `tar -x` keeps)
        and {folder: {'mode', 'owner'}} for every folder above it the tar holds (relative to the tar's root, which is
        the install root). None, {} for anything that is not a tar member."""
        archive, _, member = (source or '').partition('!')
        if not member or not self.rows.get(row):
            return None, {}
        members = self._members(Path(self.rows[row]) / archive)
        info = members.get(member)
        if info is None:
            return None, {}
        attrs = lambda m: {'mode': '%04o' % (m.mode & 0o7777), 'owner': '%d:%d' % (m.uid, m.gid)}
        dirs, parent = {}, os.path.dirname(member)
        while parent:
            m = members.get(parent) or members.get(parent + '/')
            if m is not None and m.isdir():
                dirs[parent] = attrs(m)
            parent = os.path.dirname(parent)
        return dict(attrs(info), mtime=int(info.mtime)), dirs

    def row_tree(self, row, path, source=None):
        src = self.rows.get(row)
        if not src or not Path(src).is_dir():
            raise Unresolved('row %s is not a folder' % row)
        # never the whole row: a tree is the folder named by source, by its install path, or by its own name
        for cand in ([Path(src) / source] if source else
                     [Path(src) / path, Path(src) / 'root' / path, Path(src) / os.path.basename(path)]):
            if cand.is_dir() and any(cand.rglob('*')):
                return {str(Path(path, f.relative_to(cand))): f.read_bytes()
                        for f in sorted(cand.rglob('*')) if f.is_file() and not f.is_symlink()}
        raise Unresolved('row %s has no folder for %s' % (row, path))


def sign(data, sign_spec, original, sources=None):
    """DATA signed ad hoc as SIGN_SPEC says, once; ORIGINAL gives a preserved identifier, SOURCES the file whose
    entitlements SIGN_SPEC's entitlements_from names."""
    ents = None
    if sign_spec.get('entitlements_from'):
        if sources is None:
            raise Unresolved('sign takes the entitlements of another file and no sources were given')
        ents = sources.entitlements_source(sign_spec['entitlements_from'])
    try:
        return SIGNING.sign(data, sign_spec, original, ents, SPEC.codesign_args)
    except SIGNING.SignatureError as x:
        raise Unresolved(str(x))


def external_after(spec):
    """Installed paths SPEC's external ops read on the target (spec.EXTERNAL_INPUTS)."""
    out = []
    for e in spec['edits']:
        if e['kind'] == 'op':
            for arg in SPEC.EXTERNAL_INPUTS.get(e['op'], ()):
                if e.get('args', {}).get(arg):
                    out.append(e['args'][arg])
    return out


def compose(entry, root, sources, store, repo, facts=None):
    """{path: bytes} for one ledger entry (a tree origin yields many paths). Edited files are signed once, after
    the last edit, as the last signing spec says. FACTS (a dict) gets 'base': {'path', 'sha256'} of the file on the
    target the first edit starts from (Golden Gate's, a donor's or OCLP's), when the composition read one."""
    facts = {} if facts is None else facts
    data, original, signing = None, None, None
    after = [a for w in entry['writers'] for a in (w.get('after') or [])]
    for w in entry['writers']:
        if w['action'] == 'origin':
            o = w['origin']
            if not o.get('row'):
                raise Deferred('%s generates %s on the target' % (w['recipe'], entry['path']), after)
            if w.get('tree'):
                return sources.row_tree(o['row'], entry['path'], w.get('source'))
            data = sources.row_file(o['row'], entry['path'], w.get('source'))
            continue
        spec = SPEC.load(Path(root) / w['recipe'] / w['spec'])
        external = [e['op'] for e in spec['edits'] if e['kind'] == 'op' and e['op'] in SPEC.EXTERNAL_OPS]
        if external:
            raise Deferred('%s is made on the target (%s)' % (entry['path'], ', '.join(sorted(set(external)))),
                           external_after(spec) + after)
        if data is None:
            inp, own = w.get('input'), entry.get('own_tree')
            if own and not inp:                       # the file the recipe's own tree installs
                tree = sources.row_tree(own['row'], own['tree'], own.get('source')) if own.get('row') else {}
                if entry['path'] not in tree:
                    raise Unresolved('%s: not in %s tree %s' % (entry['path'], own['recipe'], own['tree']))
                data = tree[entry['path']]
            elif inp and inp.get('row'):
                data = sources.row_file(inp['row'], entry['path'])
            elif inp:
                data = sources.base(inp['path'], inp['kind'])
                facts['base'] = {'path': inp['path'], 'sha256': hashlib.sha256(data).hexdigest()}
            else:
                data = sources.base(entry['path'], entry['base']['kind'])
                facts['base'] = {'path': entry['path'], 'sha256': hashlib.sha256(data).hexdigest()}
        original = data if original is None else original
        data, _ = ENGINE.apply_stored(spec, data, store, repo=str(repo))
        signing = spec.get('sign') or signing
    if signing:
        data = sign(data, signing, original, sources)
    return {entry['path']: data}


def origin_attrs(entry, sources):
    """(attributes, folders) the file's last whole-file origin installs it with, when nothing edits it after that
    origin and the origin is a tar member (member_attrs); else (None, {})."""
    last = entry['writers'][-1]
    if last['action'] != 'origin' or last.get('tree') or not last['origin'].get('row'):
        return None, {}
    try:
        return sources.member_attrs(last['origin']['row'], last.get('source'))
    except (OSError, tarfile.TarError):
        return None, {}


def replaces(entry, sources):
    """{'base': {'path', 'sha256', 'edited': False}} for a whole file that replaces one a layer installed (what the
    recipe's hook expects to find there), or {} when no layer has it or it can't be read here."""
    try:
        data = sources.base(entry['path'], entry['base']['kind'])
    except (Unresolved, Deferred, OSError):
        return {}
    return {'base': {'path': entry['path'], 'sha256': hashlib.sha256(data).hexdigest(), 'edited': False}}


def deferred_order(deferred):
    """DEFERRED ([{path, after}]) in an order where every path comes after what it names: a path, or OVERLAYS,
    which means after every overlay file and every other deferred path that does not itself wait for OVERLAYS (the
    target's whole System as built)."""
    pending = {d['path']: d for d in deferred}
    done, out = set(), []
    while pending:
        last = {p for p, d in pending.items() if OVERLAYS in d.get('after', [])}
        ready = sorted(p for p, d in pending.items()
                       if all(a in done or a not in pending for a in d.get('after', []) if a != OVERLAYS)
                       and (OVERLAYS not in d.get('after', []) or not set(pending) - last))
        if not ready:                     # a cycle: report it in path order rather than loop
            ready = sorted(pending)
        for p in ready:
            out.append(pending.pop(p))
            done.add(p)
    return out


def materialize(ledger, root, sources, out, store_dir, pack_of, phase_of, *, repo=TOP, on_event=None, only=None):
    try:
        return _materialize(ledger, root, sources, out, store_dir, pack_of, phase_of, repo, on_event, only)
    finally:
        sources.close()


def _materialize(ledger, root, sources, out, store_dir, pack_of, phase_of, repo, on_event, only):
    emit = on_event or (lambda e: None)
    out = Path(out)
    store = STORE.Store(store_dir)
    manifests, report = {}, []
    for path, entry in sorted(ledger.items()):
        if only and path not in only:
            continue
        last = entry['writers'][-1]['recipe']
        pack = pack_of(last)
        facts = {}
        try:
            files = compose(entry, root, sources, store, repo, facts)
        except Deferred as x:
            report.append({'path': path, 'outcome': 'deferred', 'reason': str(x), 'pack': pack, 'recipe': last,
                           'after': x.after})
            emit({'ev': 'deferred', 'path': path, 'reason': str(x)})
            continue
        except (Unresolved, ENGINE.EditError, OSError) as x:
            report.append({'path': path, 'outcome': 'unresolved', 'reason': str(x), 'pack': pack})
            emit({'ev': 'unresolved', 'path': path, 'reason': str(x)})
            continue
        own = sorted(p for p in files if p != path and p in ledger)    # declared on their own: made there
        for p in own:
            del files[p]
        if pack is None:
            report.append({'path': path, 'outcome': 'unpacked', 'reason': '%s is in no pack' % last, 'pack': None})
            continue
        m = manifests.setdefault(pack, {'format': 1, 'pack': pack, 'phase': phase_of(pack), 'files': {}})
        install = next((w.get('install') for w in reversed(entry['writers']) if w.get('install')), None) or {}
        member, dirs = origin_attrs(entry, sources)
        member = member or {}
        for p, data in files.items():
            dest = out / pack / 'files' / p
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
            m['files'][p] = {'sha256': hashlib.sha256(data).hexdigest(),
                             'mode': install.get('mode') or member.get('mode') or
                                     ('0755' if data[:4] in MACHO else '0644'),
                             'owner': install.get('owner') or member.get('owner') or 'root:wheel',
                             'flags': install.get('flags', [])}
            if not install.get('mode') and member.get('mtime') is not None:
                m['files'][p]['mtime'] = member['mtime']       # as `tar -x` leaves it
        for d, attrs in dirs.items():
            m.setdefault('dirs', {})[d] = attrs
        r = {'path': path, 'outcome': 'made', 'files': len(files), 'pack': pack, 'recipe': last,
             'writers': [w['recipe'] for w in entry['writers']], 'paths': sorted(files)}
        if facts.get('base'):
            r['base'] = dict(facts['base'], edited=True)      # the bytes the first edit reads must be these
        elif len(files) == 1 and path in files:
            r.update(replaces(entry, sources))
        if own:
            r['declared_separately'] = own
        report.append(r)
        emit({'ev': 'made', 'path': path, 'pack': pack, 'files': len(files)})
    for pack, m in manifests.items():
        (out / pack).mkdir(parents=True, exist_ok=True)
        (out / pack / 'manifest.json').write_text(json.dumps(m, indent=1, sort_keys=True) + '\n')
    (out / 'report.json').write_text(json.dumps(report, indent=1) + '\n')
    order = deferred_order([{k: r[k] for k in ('path', 'recipe', 'pack', 'reason', 'after')}
                            for r in report if r['outcome'] == 'deferred'])
    (out / 'deferred.json').write_text(json.dumps(order, indent=1) + '\n')
    return report


def repo_rows(build, golden=None):
    """{row: path} from pipeline/inputs/golden/* (only golden/GOLDEN when given, as golden-run reads it),
    pipeline/inputs/BUILD and pipeline/inputs/common, first wins."""
    lock = runpy.run_path(str(TOP / 'pipeline' / 'lock.py'))
    rows = {}
    for folder in (TOP / 'pipeline/inputs/golden', TOP / 'pipeline/inputs' / build, TOP / 'pipeline/inputs/common'):
        for sub in (([folder / golden] if golden else [folder / d for d in sorted(os.listdir(folder))])
                    if folder.name == 'golden' and folder.is_dir() else [folder]):
            if sub.is_dir():
                for r, (_, path) in lock['rows'](str(sub)).items():
                    rows.setdefault(r, path)
    return rows


def run(setfile, target, build, out, store=None, system=None, packs='auto', only=None, golden=None):
    """(report, ledger) of materialising SET for TARGET and BUILD into OUT, as the command line does."""
    patches = runpy.run_path(str(TOP / 'pipeline' / 'patches.py'))
    ledger_mod = runpy.run_path(str(TOP / 'pipeline' / 'ledger.py'))
    defs = patches['load_packs']()
    selected = patches['detect_packs'](target, defs) if packs == 'auto' else packs.split(',')
    ledger = ledger_mod['build'](setfile, target, build)
    rows = repo_rows(build, golden)
    member = {r: n for n, d in defs.items() for r in d.get('recipes', [])}
    pack_of = lambda recipe: member.get(recipe) if member.get(recipe) in selected else None
    phase_of = lambda pack: defs[pack]['phase']
    report = materialize(ledger, TOP / 'patches', Sources.from_repo(rows, system), out,
                         store or str(Path(out) / 'store'), pack_of, phase_of, only=set(only) if only else None)
    return report, ledger


def main(argv):
    p = argparse.ArgumentParser(prog='materialize.py', description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('set'); p.add_argument('target'); p.add_argument('build')
    p.add_argument('--out', required=True); p.add_argument('--store'); p.add_argument('--system')
    p.add_argument('--packs', default='auto'); p.add_argument('--json', action='store_true')
    p.add_argument('--golden', metavar='BUILD', help='read Golden Gate rows from pipeline/inputs/golden/BUILD only')
    p.add_argument('--only', action='append', metavar='PATH', help='make only this declared path (repeatable)')
    a = p.parse_args(argv)
    report, _ = run(a.set, a.target, a.build, a.out, a.store, a.system, a.packs, a.only, a.golden)
    counts = {}
    for r in report:
        counts[r['outcome']] = counts.get(r['outcome'], 0) + 1
    if a.json:
        print(json.dumps({'counts': counts, 'report': report}, indent=1))
    else:
        for r in report:
            if r['outcome'] != 'made':
                print('%-10s %s: %s%s' % (r['outcome'], r['path'], r['reason'],
                                          ' (after %s)' % ', '.join(r['after']) if r.get('after') else ''))
        print('\nmaterialize: %s' % ', '.join('%d %s' % (n, k) for k, n in sorted(counts.items())))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
