#!/usr/bin/python3 -I
"""materialize: make each declared file once, into pack overlays that hao patch and the installer apply.

  materialize.py SET TARGET BUILD --out DIR [--store DIR] [--system ROOT] [--packs auto|A,B] [--json]

The ledger (pipeline/ledger.py) gives every declared path its base and its writers in set order. Each path is
composed once: the base bytes when the first writer edits (OCLP's payload through its install map, Golden
Gate's system at --system, or a Tahoe donor payload), then each writer in turn: a whole file from its input row,
or an edit spec applied by tools/edit-engine (through --store, so an unchanged spec and input are reused by hash),
and an edited file is signed ad hoc once, after its last edit (macOS codesign).
The result goes to DIR/<pack>/files/<path>, and DIR/<pack>/manifest.json records sha256, mode, owner and flags
(the format tools/hao reads). A path is "deferred" when it can only be made on the target (kc.link, cryptex.build,
hao.embed-stub, files the hook generates) and "unresolved" when its source isn't found; both are reported with
the reason, never guessed. DIR/report.json lists every path's outcome.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import runpy
import shutil
import sys

TOP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOP / 'tools' / 'edit-engine' / 'src'))
import engine as ENGINE     # noqa: E402
import spec as SPEC         # noqa: E402
import store as STORE       # noqa: E402

MACHO = (b'\xcf\xfa\xed\xfe', b'\xca\xfe\xba\xbe', b'\xca\xfe\xba\xbf')


class Unresolved(Exception):
    pass


class Deferred(Exception):
    pass


class Sources:
    """Where base files and input rows come from. Built from the repository's rows, or given directly in tests."""

    def __init__(self, rows=None, oclp_payload=None, oclp_map=None, system=None, tahoe_roots=()):
        self.rows = rows or {}                     # row -> path (file or folder)
        self.oclp_payload = oclp_payload           # OCLP hardware artifact's payload folder
        self.oclp_map = oclp_map or []             # [(destination, operation, support_package_relative_path)]
        self.system = system                       # Golden Gate's own system root
        self.tahoe_roots = list(tahoe_roots)       # donor payload roots, each rooted at /

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
                import tarfile
                try:
                    with tarfile.open(src / archive) as t:
                        f = t.extractfile(member) or t.extractfile('./' + member)
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


def sign(data, sign_spec, original):
    """DATA signed ad hoc as SIGN_SPEC says, once (codesign on a temporary copy); ORIGINAL gives a preserved
    identifier."""
    import subprocess
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        existing = None
        if 'identifier' in sign_spec.get('preserve', []):
            Path(tmp, 'original').write_bytes(original)
            shown = subprocess.run(['codesign', '-dv', str(Path(tmp, 'original'))], capture_output=True, text=True)
            existing = next((l.split('=', 1)[1] for l in shown.stderr.splitlines() if l.startswith('Identifier=')), None)
        Path(tmp, 'file').write_bytes(data)
        r = subprocess.run(SPEC.codesign_args(sign_spec, existing) + [str(Path(tmp, 'file'))], capture_output=True,
                           text=True)
        if r.returncode:
            raise Unresolved('codesign failed: %s' % r.stderr.strip()[:200])
        return Path(tmp, 'file').read_bytes()


def compose(entry, root, sources, store, repo):
    """{path: bytes} for one ledger entry (a tree origin yields many paths). Edited files are signed once, after
    the last edit, as the last signing spec says."""
    data, original, signing = None, None, None
    for w in entry['writers']:
        if w['action'] == 'origin':
            o = w['origin']
            if not o.get('row'):
                raise Deferred('%s generates %s on the target' % (w['recipe'], entry['path']))
            if w.get('tree'):
                return sources.row_tree(o['row'], entry['path'], w.get('source'))
            data = sources.row_file(o['row'], entry['path'], w.get('source'))
            continue
        spec = SPEC.load(Path(root) / w['recipe'] / w['spec'])
        external = [e['op'] for e in spec['edits'] if e['kind'] == 'op' and e['op'] in SPEC.EXTERNAL_OPS]
        if external:
            raise Deferred('%s is made on the target (%s)' % (entry['path'], ', '.join(sorted(set(external)))))
        if data is None:
            inp = w.get('input')
            data = sources.base(inp['path'], inp['kind']) if inp else sources.base(entry['path'], entry['base']['kind'])
        original = data if original is None else original
        data, _ = ENGINE.apply_stored(spec, data, store, repo=str(repo))
        signing = spec.get('sign') or signing
    if signing:
        data = sign(data, signing, original)
    return {entry['path']: data}


def materialize(ledger, root, sources, out, store_dir, pack_of, phase_of, *, repo=TOP, on_event=None):
    emit = on_event or (lambda e: None)
    out = Path(out)
    store = STORE.Store(store_dir)
    manifests, report = {}, []
    for path, entry in sorted(ledger.items()):
        last = entry['writers'][-1]['recipe']
        pack = pack_of(last)
        try:
            files = compose(entry, root, sources, store, repo)
        except Deferred as x:
            report.append({'path': path, 'outcome': 'deferred', 'reason': str(x), 'pack': pack})
            emit({'ev': 'deferred', 'path': path, 'reason': str(x)})
            continue
        except (Unresolved, ENGINE.EditError, OSError) as x:
            report.append({'path': path, 'outcome': 'unresolved', 'reason': str(x), 'pack': pack})
            emit({'ev': 'unresolved', 'path': path, 'reason': str(x)})
            continue
        if pack is None:
            report.append({'path': path, 'outcome': 'unpacked', 'reason': '%s is in no pack' % last, 'pack': None})
            continue
        m = manifests.setdefault(pack, {'format': 1, 'pack': pack, 'phase': phase_of(pack), 'files': {}})
        install = next((w.get('install') for w in reversed(entry['writers']) if w.get('install')), None) or {}
        for p, data in files.items():
            dest = out / pack / 'files' / p
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
            m['files'][p] = {'sha256': hashlib.sha256(data).hexdigest(),
                             'mode': install.get('mode') or ('0755' if data[:4] in MACHO else '0644'),
                             'owner': install.get('owner', 'root:wheel'), 'flags': install.get('flags', [])}
        report.append({'path': path, 'outcome': 'made', 'files': len(files), 'pack': pack})
        emit({'ev': 'made', 'path': path, 'pack': pack, 'files': len(files)})
    for pack, m in manifests.items():
        (out / pack).mkdir(parents=True, exist_ok=True)
        (out / pack / 'manifest.json').write_text(json.dumps(m, indent=1, sort_keys=True) + '\n')
    (out / 'report.json').write_text(json.dumps(report, indent=1) + '\n')
    return report


def main(argv):
    p = argparse.ArgumentParser(prog='materialize.py', description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('set'); p.add_argument('target'); p.add_argument('build')
    p.add_argument('--out', required=True); p.add_argument('--store'); p.add_argument('--system')
    p.add_argument('--packs', default='auto'); p.add_argument('--json', action='store_true')
    a = p.parse_args(argv)
    patches = runpy.run_path(str(TOP / 'pipeline' / 'patches.py'))
    ledger_mod = runpy.run_path(str(TOP / 'pipeline' / 'ledger.py'))
    lock = runpy.run_path(str(TOP / 'pipeline' / 'lock.py'))
    defs = patches['load_packs']()
    selected = patches['detect_packs'](a.target, defs) if a.packs == 'auto' else a.packs.split(',')
    ledger = ledger_mod['build'](a.set, a.target, a.build)
    rows = {}
    for folder in (TOP / 'pipeline/inputs/golden', TOP / 'pipeline/inputs' / a.build, TOP / 'pipeline/inputs/common'):
        for sub in ([folder / d for d in sorted(os.listdir(folder))] if folder.name == 'golden' and folder.is_dir()
                    else [folder]):
            if sub.is_dir():
                for r, (_, path) in lock['rows'](str(sub)).items():
                    rows.setdefault(r, path)
    member = {r: n for n, d in defs.items() for r in d.get('recipes', [])}
    pack_of = lambda recipe: member.get(recipe) if member.get(recipe) in selected else None
    phase_of = lambda pack: defs[pack]['phase']
    report = materialize(ledger, TOP / 'patches', Sources.from_repo(rows, a.system), a.out,
                         a.store or str(Path(a.out) / 'store'), pack_of, phase_of)
    counts = {}
    for r in report:
        counts[r['outcome']] = counts.get(r['outcome'], 0) + 1
    if a.json:
        print(json.dumps({'counts': counts, 'report': report}, indent=1))
    else:
        for r in report:
            if r['outcome'] != 'made':
                print('%-10s %s: %s' % (r['outcome'], r['path'], r['reason']))
        print('\nmaterialize: %s' % ', '.join('%d %s' % (n, k) for k, n in sorted(counts.items())))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
