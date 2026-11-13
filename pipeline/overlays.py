#!/usr/bin/python3 -I
"""overlays: an image build that installs the materialised pack overlays in place of the recipes' hooks (opt-in).

  overlays.py plan SET TARGET BUILD --overlays DIR [--patches PATCHES.txt] [--system ROOT] [--hook NAME]... [--json]
  overlays.py tree ROOT --out TREE.json [--skip PATH]...
  overlays.py compare HOOK.json OVERLAY.json [--hook-root R --overlay-root R] [--overlays DIR] [--noise N.json]
                      [--json OUT]

`golden-run --from-overlays` materialises the set (pipeline/materialize.py, its edit-engine store kept in
store/cache so an unchanged spec and input are reused) and calls schedule() on the result. Each recipe of the set,
in set order, is then one of:

  overlay  every file it declares was made on the host and nothing it writes waits for the target: its hook does
           not run; the guest (pipeline/stages/golden-overlays.py) installs its files, checked by sha256, with the
           manifest's mode, owner, flags (and a tar member's mtime), and creates every folder its tar
           holds, empty ones included, as its `tar -x` would
  hook     anything else (no declarations, a path deferred to the target or unresolved, a writer the set skips, a
           post recipe writing System, or --hook NAME): its hook runs exactly as without --from-overlays, and so do
           the deferred steps inside it (kernel collections, cryptex, HaoExec stamp, kc.bind, native-identities)

A recipe that writes a file before a hook recipe edits it is a hook recipe too (the overlay holds only the final
file, not the one that hook expects), so on any path an overlay recipe only ever follows hook recipes. A file is
installed at its last writer's turn; when every writer of it is an overlay recipe, the guest first checks it finds the
file the materialiser started from (report.json "base": an edit's input must match, a replaced file is recorded).
A deferred step that waits for "overlays" (native-identities) must come after every System file the overlays
install, and a step waiting for a path after it; schedule() refuses a set where it would not.

`tree` lists a mounted image volume (read-only) as {path: type, sha256, size, mode, uid, gid, flags, link, mtime};
`compare` sets a hook-built tree beside an overlay-built one: equal, signature-only (tools/edit-engine signing
compare(): only the CDHash, signature size or padding differ), derived (made on the target from installed CDHashes:
native-identities and its record, compared without the observed CDHashes), noise (differs between two hook builds as well,
from a hook-vs-hook compare given as --noise) and real differences. An overlay build is equal to its hook build when
compare lists no real difference.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import sys

TOP = Path(__file__).resolve().parents[1]
OVERLAYS = 'overlays'                     # an "after" entry (pipeline/materialize.py)
PLAN_FORMAT = 1
# What a post recipe (System read-only) may write: the EFI partition, and Data through the image's firmlinks.
EFI = 'EFI/'
NOT_SYSTEM = ('EFI/', 'Preboot/', 'System/Volumes/')
MACHO = (b'\xcf\xfa\xed\xfe', b'\xca\xfe\xba\xbe', b'\xca\xfe\xba\xbf')
DERIVED_COLUMNS = {'usr/lib/hao/native-identities': 0}     # column keyed on installed CDHashes (observed)
# golden-gate-native-identities' record: the same rows, keyed on installed CDHashes, made in a temporary folder
DERIVED_KEYS = {'System/Library/CoreServices/golden-gate-native-identities.json': ('observed', 'list_sha256', 'sources')}
# ... whose temporary folder (kc-hook.sh's mktemp) also appears inside each row's "source" and the skip reasons
DERIVED_TEMP = {'System/Library/CoreServices/golden-gate-native-identities.json':
                re.compile(rb'/private/var/tmp/native-identities\.[A-Za-z0-9]+')}


class PlanError(ValueError):
    pass


def read_rows(patches_txt):
    """[(stage, name)] of SHARE/patches.txt (pipeline/patches.py plan): the recipes the build runs, in order."""
    rows = []
    for line in Path(patches_txt).read_text().splitlines():
        parts = line.split()
        if parts and not parts[0].startswith('#'):
            rows.append((parts[0], parts[1]))
    return rows


def firmlinks(system):
    """Folders a firmlink puts on the Data volume, from SYSTEM's usr/share/firmlinks (relative, no slashes)."""
    f = Path(system or '', 'usr/share/firmlinks') if system else None
    if not f or not f.is_file():
        return None
    return sorted({line.split('\t')[0].strip('/') for line in f.read_text().splitlines() if line.strip()})


def under(path, root):
    return path == root or path.startswith(root.rstrip('/') + '/')


def volume_of(path, stage, links):
    """The volume a recipe of STAGE writes PATH on: 'system' for every oclp-hook path (its hook writes SYSTEM/PATH),
    for a post 'esp' (EFI/...), 'data' (under a firmlink) or 'system'; None where an installer may not write it."""
    if stage == 'oclp-hook':
        return None if path.startswith(NOT_SYSTEM) else 'system'
    if path.startswith(EFI):
        return 'esp'
    if path.startswith(NOT_SYSTEM):
        return None
    if links is not None and any(under(path, f) for f in links):
        return 'data'
    return 'system'


def schedule(rows, ledger, report, manifests, *, force_hook=(), links=None):
    """The install plan for ROWS ([(stage, name)] in run order): {'format', 'steps': [{name, stage, mode, reason,
    files: [...], dirs}], 'deferred': [...], 'counts'}. LEDGER is pipeline/ledger.py build(), REPORT
    materialize()'s report, MANIFESTS {pack: manifest}, LINKS the image's firmlinks (firmlinks())."""
    order = {name: i for i, (_, name) in enumerate(rows)}
    stage_of = dict((name, stage) for stage, name in rows)
    by_path = {r['path']: r for r in report}
    writes = {}
    for path, e in ledger.items():
        for w in e['writers']:
            writes.setdefault(w['recipe'], []).append(path)
    mode, reason = {}, {}
    for stage, name in rows:
        paths = sorted(writes.get(name, []))
        why = None
        if name in force_hook:
            why = 'kept on its hook (--hook)'
        elif stage not in ('oclp-hook', 'post'):
            why = 'stage %s' % stage
        elif not paths:
            why = 'declares no files'
        else:
            for p in paths:
                r = by_path.get(p)
                if r is None or r['outcome'] != 'made':
                    why = '%s is %s%s' % (p, r['outcome'] if r else 'not materialised',
                                          ': ' + r['reason'] if r and r.get('reason') else '')
                    break
                foreign = [w['recipe'] for w in ledger[p]['writers'] if w['recipe'] not in order]
                if foreign:
                    why = '%s is also written by %s, which this build leaves out' % (p, ', '.join(foreign))
                    break
                for f in r.get('paths', [p]) + r.get('dirs', []):
                    v = volume_of(f, stage, links)
                    if v is None or (stage == 'post' and v == 'system'):
                        why = '%s: a %s recipe cannot install it here (%s)' % (
                            f, stage, 'System is read-only after the snapshot' if v else 'another volume')
                        break
                if why:
                    break
        mode[name] = 'hook' if why else 'overlay'
        reason[name] = why or 'every declared file made on the host (%d)' % len(paths)
    changed = True
    while changed:                                 # an overlay writer never precedes a hook writer of the same path
        changed = False
        for path, e in sorted(ledger.items()):
            names = [w['recipe'] for w in e['writers'] if w['recipe'] in order]
            for i, a in enumerate(names):
                later = next((b for b in names[i + 1:] if mode[b] == 'hook'), None)
                if mode[a] == 'overlay' and later:
                    mode[a], reason[a] = 'hook', '%s: %s edits it after on its hook' % (path, later)
                    changed = True
    files_of = {name: [] for _, name in rows}
    meta = {}
    for pack, m in manifests.items():
        for p, f in m['files'].items():
            meta[p] = dict(f, pack=pack)
    for r in report:
        if r['outcome'] != 'made' or r.get('recipe') not in order or mode[r['recipe']] != 'overlay':
            continue
        check = r.get('base') if all(mode.get(w) == 'overlay' for w in r.get('writers', [r['recipe']])) else None
        if check and check['path'] != r['path'] and check['path'] in ledger:
            check = None                           # another declared file: changed by the time this installs
        for p in r.get('paths', [r['path']]):
            if p not in meta:
                raise PlanError('%s was made but is in no manifest' % p)
            row = dict(meta[p], path=p, volume=volume_of(p, stage_of[r['recipe']], links))
            if check and p == r['path']:
                row['base'] = check
            files_of[r['recipe']].append(row)
    dirs = {}
    for m in manifests.values():
        for d, a in m.get('dirs', {}).items():
            dirs.setdefault(d, a)
    created = {}                                   # recipe -> the folders its `tar -x` creates (report "dirs")
    for r in report:
        if r['outcome'] == 'made' and r.get('dirs'):
            created.setdefault(r.get('recipe'), set()).update(r['dirs'])
    steps = []
    for stage, name in rows:
        step = {'name': name, 'stage': stage, 'mode': mode[name], 'reason': reason[name]}
        if mode[name] == 'overlay':
            step['files'] = sorted(files_of[name], key=lambda f: f['path'])
            step['dirs'] = {d: dict(a, volume=volume_of(d, stage, links)) for d, a in sorted(dirs.items())
                            if d in created.get(name, ()) or any(f['path'].startswith(d + '/') for f in step['files'])}
        steps.append(step)
    deferred = [dict(r, runs_in=r.get('recipe')) for r in report if r['outcome'] == 'deferred'
                and r.get('recipe') in order]
    _check_deferred(steps, deferred, order)
    counts = {'overlay': sum(1 for s in steps if s['mode'] == 'overlay'),
              'hook': sum(1 for s in steps if s['mode'] == 'hook'),
              'files': sum(len(s.get('files', [])) for s in steps)}
    return {'format': PLAN_FORMAT, 'steps': steps, 'deferred': deferred, 'counts': counts}


def _check_deferred(steps, deferred, order):
    """Refuse a plan where a deferred step would run before what it waits for."""
    installed_at = {f['path']: order[s['name']] for s in steps for f in s.get('files', [])}
    system_after = {}
    for s in steps:
        for f in s.get('files', []):
            if f['volume'] == 'system':
                system_after[s['name']] = order[s['name']]
    deferred_at = {d['path']: order[d['runs_in']] for d in deferred}
    for d in deferred:
        at = order[d['runs_in']]
        for a in d.get('after') or []:
            if a == OVERLAYS:
                late = sorted(n for n, i in system_after.items() if i > at)
                if late:
                    raise PlanError('%s waits for every overlay file, but %s install System files after %s runs'
                                    % (d['path'], ', '.join(late), d['runs_in']))
            elif installed_at.get(a, -1) > at or deferred_at.get(a, -1) > at:
                raise PlanError('%s waits for %s, which is made after %s runs' % (d['path'], a, d['runs_in']))


def load_manifests(overlays):
    return {m['pack']: m for m in (json.loads(p.read_text()) for p in sorted(Path(overlays).glob('*/manifest.json')))}


def stage_files(plan, overlays, dest):
    """Link (else copy) every planned file from OVERLAYS/<pack>/files into DEST/<path>; returns the byte count."""
    total = 0
    for step in plan['steps']:
        for f in step.get('files', []):
            src = Path(overlays, f['pack'], 'files', f['path'])
            out = Path(dest, f['path'])
            out.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.link(src, out)
            except OSError:
                shutil.copyfile(src, out)
            total += out.stat().st_size
    return total


def render(plan):
    lines = []
    for s in plan['steps']:
        lines.append('%-8s %-9s %-36s %s' % (s['mode'], s['stage'], s['name'],
                                            '%d files' % len(s['files']) if s['mode'] == 'overlay' else s['reason']))
    c = plan['counts']
    lines.append('\noverlays: %d recipes from overlays (%d files), %d on their hooks, %d deferred paths in hooks'
                 % (c['overlay'], c['files'], c['hook'], len(plan['deferred'])))
    return '\n'.join(lines)


# --- tree manifests and the comparison ---

def sha256_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


FLAG_NAMES = {0x80000: 'restricted', 0x20000: 'schg', 0x100000: 'sunlnk', 0x2: 'uchg', 0x8000: 'hidden',
              0x1: 'nodump', 0x20: 'compressed'}


def tree(root, skip=()):
    """{'root', 'entries': {path: {...}}, 'unreadable': [path]} for everything under ROOT on ROOT's own volume."""
    root = Path(root)
    device = os.lstat(root).st_dev
    entries, unreadable = {}, []
    stack = ['']
    while stack:
        rel = stack.pop()
        folder = root / rel if rel else root
        try:
            items = sorted(os.scandir(folder), key=lambda e: e.name)
        except OSError:
            unreadable.append(rel or '.')
            continue
        for item in items:
            path = rel + '/' + item.name if rel else item.name
            if any(under(path, s) for s in skip):
                continue
            try:
                st = item.stat(follow_symlinks=False)
            except OSError:
                unreadable.append(path)
                continue
            e = {'mode': '%04o' % stat.S_IMODE(st.st_mode), 'uid': st.st_uid, 'gid': st.st_gid,
                 'flags': sorted(n for b, n in FLAG_NAMES.items() if getattr(st, 'st_flags', 0) & b and
                                 n != 'compressed')}
            if stat.S_ISLNK(st.st_mode):
                e.update(type='l', link=os.readlink(item.path))
            elif stat.S_ISDIR(st.st_mode):
                e['type'] = 'd'
                if st.st_dev == device:
                    stack.append(path)
            elif stat.S_ISREG(st.st_mode):
                e.update(type='f', size=st.st_size, mtime=int(st.st_mtime))
                try:
                    e['sha256'] = sha256_file(item.path)
                except OSError:
                    unreadable.append(path)
            else:
                e['type'] = 'o'
            entries[path] = e
    return {'root': str(root), 'entries': entries, 'unreadable': sorted(unreadable)}


def _masked(data, column):
    out = []
    for line in data.decode(errors='replace').splitlines():
        parts = line.split()
        out.append(' '.join(p for i, p in enumerate(parts) if i != column) if parts and not
                   line.startswith('#') else line)
    return out


def _without(data, keys):
    def strip(v):
        if isinstance(v, dict):
            return {k: strip(x) for k, x in v.items() if k not in keys}
        return [strip(x) for x in v] if isinstance(v, list) else v
    try:
        return strip(json.loads(data))
    except ValueError:
        return None


def _derived(path, a, b):
    if path in DERIVED_COLUMNS:
        return _masked(a, DERIVED_COLUMNS[path]) == _masked(b, DERIVED_COLUMNS[path])
    if path in DERIVED_KEYS:
        if path in DERIVED_TEMP:
            a, b = (DERIVED_TEMP[path].sub(b'TEMP', x) for x in (a, b))
        x = _without(a, DERIVED_KEYS[path])
        return x is not None and x == _without(b, DERIVED_KEYS[path])
    return False


def _signature_only(a, b):
    sys.path.insert(0, str(TOP / 'tools' / 'edit-engine' / 'src'))
    import signing
    try:
        r = signing.compare(a, b)
    except Exception as x:                                  # not Mach-O, or unreadable as one
        return False, str(x)
    return not r['problems'], '; '.join(r['problems'] or r['notes'])


ATTRS = ('type', 'mode', 'uid', 'gid', 'flags', 'link')


def compare(hook, overlay, hook_root=None, overlay_root=None, overlay_manifests=None, noise=None):
    """{'counts', 'differences': [{path, kind, detail}]} of OVERLAY's tree against HOOK's. Kinds: signature-only,
    derived, noise (listed in NOISE, a previous compare of two hook builds), and the real ones: content, attrs,
    mtime (a tar member the manifest dates), only-hook, only-overlay, unreadable."""
    a, b = hook['entries'], overlay['entries']
    mtimes = {p: f['mtime'] for m in (overlay_manifests or {}).values() for p, f in m['files'].items()
              if 'mtime' in f}
    noisy = {d['path'] for d in (noise or {}).get('differences', []) if d['kind'] not in ('noise',)}
    out = []
    for path in sorted(set(a) | set(b)):
        x, y = a.get(path), b.get(path)
        diff = None
        if x is None or y is None:
            diff = ('only-overlay' if x is None else 'only-hook', '')
        elif [x.get(k) for k in ATTRS] != [y.get(k) for k in ATTRS]:
            diff = ('attrs', ', '.join('%s %r -> %r' % (k, x.get(k), y.get(k)) for k in ATTRS if x.get(k) != y.get(k)))
        elif x['type'] == 'f' and (x.get('sha256') is None or y.get('sha256') is None):
            diff = ('unreadable', 'no sha256 on one side')
        elif x['type'] == 'f' and x['sha256'] != y['sha256']:
            diff = ('content', '%s -> %s' % (x['sha256'][:16], y['sha256'][:16]))
            if hook_root and overlay_root:
                da, db = Path(hook_root, path).read_bytes(), Path(overlay_root, path).read_bytes()
                if _derived(path, da, db):
                    diff = ('derived', 'equal without what is keyed on installed CDHashes')
                else:
                    same, why = _signature_only(da, db)
                    diff = ('signature-only', why) if same else ('content', diff[1] + (' (%s)' % why if
                                                                                        da[:4] in MACHO else ''))
        elif x['type'] == 'f' and path in mtimes and y.get('mtime') != mtimes[path]:
            diff = ('mtime', '%s, manifest %s' % (y.get('mtime'), mtimes[path]))
        if diff and diff[0] not in ('signature-only', 'derived') and path in noisy:
            diff = ('noise', diff[0] + ': ' + diff[1])
        if diff:
            out.append({'path': path, 'kind': diff[0], 'detail': diff[1]})
    for u in sorted(set(hook.get('unreadable', [])) | set(overlay.get('unreadable', []))):
        out.append({'path': u, 'kind': 'unreadable', 'detail': 'could not be read'})
    counts = {}
    for d in out:
        counts[d['kind']] = counts.get(d['kind'], 0) + 1
    counts['equal'] = len(set(a) & set(b)) - sum(1 for d in out if d['path'] in a and d['path'] in b)
    real = [d for d in out if d['kind'] not in ('signature-only', 'derived', 'noise')]
    return {'counts': counts, 'real': len(real), 'differences': out}


def main(argv):
    p = argparse.ArgumentParser(prog='overlays.py', description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest='command', required=True)
    pl = sub.add_parser('plan')
    pl.add_argument('set'); pl.add_argument('target'); pl.add_argument('build')
    pl.add_argument('--overlays', required=True, help='materialize.py --out DIR')
    pl.add_argument('--patches', help='the build\'s patches.txt (default: patches.py plan of SET)')
    pl.add_argument('--system', help='Golden Gate system root, for its firmlinks')
    pl.add_argument('--hook', action='append', default=[], metavar='NAME')
    pl.add_argument('--json', action='store_true')
    tr = sub.add_parser('tree')
    tr.add_argument('root'); tr.add_argument('--out', required=True)
    tr.add_argument('--skip', action='append', default=[], metavar='PATH')
    co = sub.add_parser('compare')
    co.add_argument('hook'); co.add_argument('overlay')
    co.add_argument('--hook-root'); co.add_argument('--overlay-root')
    co.add_argument('--overlays', help='the overlay build\'s materialised overlays (dates of tar members)')
    co.add_argument('--noise', help='a compare --json of two hook builds')
    co.add_argument('--json', metavar='OUT')
    a = p.parse_args(argv)
    if a.command == 'tree':
        t = tree(a.root, a.skip)
        Path(a.out).write_text(json.dumps(t, indent=0, sort_keys=True) + '\n')
        print('tree: %d entries, %d unreadable' % (len(t['entries']), len(t['unreadable'])))
        return 0
    if a.command == 'compare':
        r = compare(json.loads(Path(a.hook).read_text()), json.loads(Path(a.overlay).read_text()), a.hook_root,
                    a.overlay_root, load_manifests(a.overlays) if a.overlays else None,
                    json.loads(Path(a.noise).read_text()) if a.noise else None)
        if a.json:
            Path(a.json).write_text(json.dumps(r, indent=1) + '\n')
        for d in r['differences']:
            if d['kind'] not in ('signature-only', 'derived', 'noise'):
                print('%-13s %s  %s' % (d['kind'], d['path'], d['detail']))
        print('compare: %s; %d real' % (', '.join('%d %s' % (n, k) for k, n in sorted(r['counts'].items())), r['real']))
        return 1 if r['real'] else 0
    import runpy
    led = runpy.run_path(str(TOP / 'pipeline' / 'ledger.py'))['build'](a.set, a.target, a.build)
    if a.patches:
        rows = read_rows(a.patches)
    else:
        patches = runpy.run_path(str(TOP / 'pipeline' / 'patches.py'))
        rows = [(stage, name) for stage, name, _ in patches['plan'](a.set, a.target, a.build, locks=None)
                if stage != 'recovery']
    report = json.loads(Path(a.overlays, 'report.json').read_text())
    plan = schedule(rows, led, report, load_manifests(a.overlays), force_hook=set(a.hook), links=firmlinks(a.system))
    print(json.dumps(plan, indent=1) if a.json else render(plan))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
