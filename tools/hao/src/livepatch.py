"""Apply pack overlays to a system root: only files whose sha256 differs, checked before and after, revertible.

An overlay is one pack's materialised files: OVERLAY/manifest.json + OVERLAY/files/<path>.

  {"format": 1, "pack": NAME, "phase": "installer" | "live-capable",
   "files": {PATH: {"sha256": HEX, "mode": "0755", "owner": "root:wheel", "flags": ["restricted"]}}}

State lives in STATE (on the target: /private/var/db/hao): a lock, and one folder per applied patch holding its
record (every file's sha256 before and after, which files were new) and a backup of every replaced file, so
revert restores replaced files and removes files the patch added. A Backend snapshots and blesses after a patch
and returns to the snapshot on revert; the default DirectoryBackend does neither (a plain folder root, tests).
"""
import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil

PHASES = ('installer', 'live-capable')


class PatchError(RuntimeError):
    pass


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def load_overlay(folder):
    folder = Path(folder)
    with open(folder / 'manifest.json') as f:
        m = json.load(f)
    if m.get('format') != 1 or m.get('phase') not in PHASES or not isinstance(m.get('files'), dict):
        raise PatchError('%s: not an overlay manifest (format 1, phase, files)' % folder)
    for path, meta in m['files'].items():
        if path.startswith('/') or '..' in path.split('/'):
            raise PatchError('%s: bad path %s' % (folder, path))
        src = folder / 'files' / path
        if not src.is_file():
            raise PatchError('%s: %s is missing from files/' % (folder, path))
        if sha256_file(src) != meta['sha256']:
            raise PatchError('%s: files/%s does not match its manifest sha256' % (folder, path))
    m['folder'] = str(folder)
    return m


def plan(overlays, root, mode='live'):
    """{'write': [...], 'same': [...], 'needs_recovery': [packs]}. In live mode installer-phase packs are not
    applied (they touch kexts, the cryptex or the EFI partition) and are listed instead."""
    root = Path(root)
    out = {'write': [], 'same': [], 'needs_recovery': []}
    seen = {}
    for m in overlays:
        if mode == 'live' and m['phase'] == 'installer':
            out['needs_recovery'].append(m['pack'])
            continue
        for path, meta in sorted(m['files'].items()):
            if path in seen:
                raise PatchError('%s is in packs %s and %s' % (path, seen[path], m['pack']))
            seen[path] = m['pack']
            target = root / path
            old = sha256_file(target) if target.is_file() else None
            row = {'path': path, 'pack': m['pack'], 'source': str(Path(m['folder']) / 'files' / path),
                   'sha256': meta['sha256'], 'old_sha256': old, 'mode': meta.get('mode'),
                   'owner': meta.get('owner'), 'flags': meta.get('flags', [])}
            (out['same'] if old == meta['sha256'] else out['write']).append(row)
    return out


class DirectoryBackend:
    """No snapshots: the root is a plain folder (tests, or a volume the operator mounted read-write)."""
    name = 'directory'

    def prepare(self, root):
        if not Path(root).is_dir():
            raise PatchError('%s is not a folder' % root)

    def snapshot(self, root, name):
        return None

    def revert(self, root, record):
        return None


class MacOSLiveBackend:
    """The running system volume: mount read-write, then bless --create-snapshot with a named snapshot.
    Not proven yet (workflow plan step 9a laptop run), so it refuses to run."""
    name = 'macos-live'

    def prepare(self, root):
        raise PatchError('the live macOS backend is not proven yet: it runs only in the step 9a laptop run')

    snapshot = revert = prepare


class Lock:
    def __init__(self, state):
        self.path = Path(state) / 'lock'

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            raise PatchError('another hao patch holds %s (%s)' % (self.path, self.path.read_text().strip())) from None
        with os.fdopen(fd, 'w') as f:
            f.write('pid %d since %s\n' % (os.getpid(), datetime.datetime.now().isoformat(timespec='seconds')))
        return self

    def __exit__(self, *exc):
        self.path.unlink(missing_ok=True)


def _install(source, target, row):
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name('.hao-patch-' + target.name)
    shutil.copyfile(source, tmp)
    os.chmod(tmp, int(row['mode'], 8) if row.get('mode') else 0o644)
    if row.get('owner') and os.geteuid() == 0:
        user, _, group = row['owner'].partition(':')
        shutil.chown(tmp, user, group or None)
    os.replace(tmp, target)
    if 'restricted' in row.get('flags', []) and hasattr(os, 'chflags') and os.geteuid() == 0:
        os.chflags(target, 0x80000)   # SF_RESTRICTED


def apply(overlays, root, state, *, mode='live', backend=None, on_event=None, now=None):
    """Write every file of PLAN that differs, then snapshot. Returns the record."""
    backend = backend or DirectoryBackend()
    emit = on_event or (lambda e: None)
    root, state = Path(root), Path(state)
    backend.prepare(root)
    with Lock(state):
        p = plan(overlays, root, mode)
        emit({'ev': 'plan', 'write': len(p['write']), 'same': len(p['same']), 'needs_recovery': p['needs_recovery']})
        stamp = (now or datetime.datetime.now()).strftime('%Y%m%d-%H%M%S')
        patch_id = 'hao-%s' % stamp
        folder, n = state / 'patches' / patch_id, 1
        while folder.exists():                       # two patches in one second
            n += 1
            patch_id = 'hao-%s-%d' % (stamp, n)
            folder = state / 'patches' / patch_id
        (folder / 'backup').mkdir(parents=True)
        record = {'id': patch_id, 'root': str(root), 'mode': mode, 'backend': backend.name,
                  'packs': sorted({m['pack'] for m in overlays if m['pack'] not in p['needs_recovery']}),
                  'needs_recovery': p['needs_recovery'], 'files': [], 'state': 'writing'}
        _save(folder, record)
        for row in p['write']:
            target = root / row['path']
            now_sha = sha256_file(target) if target.is_file() else None
            if now_sha != row['old_sha256']:
                raise PatchError('%s changed while patching' % row['path'])
            if now_sha is not None:
                backup = folder / 'backup' / row['path']
                backup.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(target, backup)
            _install(row['source'], target, row)
            after = sha256_file(target)
            if after != row['sha256']:
                raise PatchError('%s: wrote %s, expected %s' % (row['path'], after, row['sha256']))
            record['files'].append({k: row[k] for k in ('path', 'pack', 'sha256', 'old_sha256')})
            _save(folder, record)
            emit({'ev': 'file.write', 'path': row['path'], 'pack': row['pack'], 'new': now_sha is None,
                  'sha256': row['sha256'], 'old_sha256': now_sha})
        record['snapshot'] = backend.snapshot(root, patch_id)
        record['state'] = 'applied'
        _save(folder, record)
        emit({'ev': 'done', 'id': patch_id, 'written': len(record['files']), 'same': len(p['same']),
              'snapshot': record['snapshot'], 'needs_recovery': p['needs_recovery']})
        return record


def _save(folder, record):
    tmp = folder / '.record.json'
    tmp.write_text(json.dumps(record, indent=1) + '\n')
    os.replace(tmp, folder / 'record.json')


def records(state):
    base = Path(state) / 'patches'
    out = []
    for d in sorted(base.iterdir()) if base.is_dir() else []:
        if (d / 'record.json').is_file():
            out.append(json.loads((d / 'record.json').read_text()))
    return out


def revert(root, state, *, patch_id=None, backend=None, on_event=None):
    """Undo the newest applied patch (or PATCH_ID): restore replaced files, remove added ones, check each."""
    backend = backend or DirectoryBackend()
    emit = on_event or (lambda e: None)
    root, state = Path(root), Path(state)
    backend.prepare(root)
    with Lock(state):
        applied = [r for r in records(state) if r['state'] == 'applied']
        if patch_id:
            applied = [r for r in applied if r['id'] == patch_id]
        if not applied:
            raise PatchError('nothing to revert%s' % (' named ' + patch_id if patch_id else ''))
        record = applied[-1]
        folder = state / 'patches' / record['id']
        for f in reversed(record['files']):
            target = root / f['path']
            if target.is_file() and sha256_file(target) != f['sha256']:
                raise PatchError('%s changed after %s: not reverting it' % (f['path'], record['id']))
        for f in reversed(record['files']):
            target = root / f['path']
            if f['old_sha256'] is None:
                target.unlink(missing_ok=True)
            else:
                backup = folder / 'backup' / f['path']
                tmp = target.with_name('.hao-revert-' + target.name)
                shutil.copy2(backup, tmp)
                os.replace(tmp, target)
                if sha256_file(target) != f['old_sha256']:
                    raise PatchError('%s: restored file does not match its recorded sha256' % f['path'])
            emit({'ev': 'file.revert', 'path': f['path'], 'removed': f['old_sha256'] is None})
        backend.revert(root, record)
        record['state'] = 'reverted'
        _save(folder, record)
        emit({'ev': 'reverted', 'id': record['id'], 'files': len(record['files'])})
        return record
