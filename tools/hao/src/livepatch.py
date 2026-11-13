"""Apply pack overlays to a system root: only what differs, checked before and after, revertible.

An overlay is one pack's materialised files: OVERLAY/manifest.json + OVERLAY/files/<path>.

  {"format": 1, "pack": NAME, "phase": "installer" | "live-capable",
   "files": {PATH: {"sha256": HEX, "mode": "0755", "owner": "root:wheel", "flags": ["restricted"]}},
   "dirs": {FOLDER: {"mode": "0700", "owner": "248:248"}}}

A file is written when its content differs, and only its attributes are set when the content is the same but its
mode, owner or flags are not the manifest's. "owner" is USER:GROUP by name (looked up in ROOT's own
private/etc/passwd and group first, then this system's) or by number (UID:GID, as `tar --numeric-owner` keeps
them). "flags" are chflags names (FLAG_BITS). "dirs" gives the mode and owner of folders hao has to create.

Owners and system flags (restricted, schg, sunlnk) can only be set by root. Without root, hao sets the mode and the
user flags, and records per file what it could not set ("not_set"; the done event counts them) rather than
failing: that is how the fixture tests run, and why a non-root run is never a proof of attributes.

State lives in STATE (on the target: /private/var/db/hao): a lock, and one folder per applied patch holding its
record (every file's sha256 and attributes before and after, which files and folders were new) and a backup of
every replaced file, so revert restores replaced files and their attributes and removes files and folders the patch
added. A Backend snapshots and blesses after a patch and returns to the snapshot on revert; the default
DirectoryBackend does neither (a plain folder root, tests).
"""
import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat

PHASES = ('installer', 'live-capable')
# chflags names -> st_flags bits (pipeline/patches.py INSTALL_FLAGS lists the same names)
FLAG_BITS = {'restricted': 0x80000, 'schg': 0x20000, 'sunlnk': 0x100000, 'uchg': 0x2, 'hidden': 0x8000,
             'nodump': 0x1}
SYSTEM_FLAGS = 0xffff0000          # only root sets these
DEFAULT_OWNER = 'root:wheel'


class PatchError(RuntimeError):
    pass


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def flag_bits(names):
    unknown = set(names) - set(FLAG_BITS)
    if unknown:
        raise PatchError('unknown flags %s (known: %s)' % (', '.join(sorted(unknown)), ', '.join(FLAG_BITS)))
    bits = 0
    for n in names:
        bits |= FLAG_BITS[n]
    return bits


def flag_names(bits):
    return [n for n, b in FLAG_BITS.items() if bits & b]


def load_overlay(folder):
    folder = Path(folder)
    with open(folder / 'manifest.json') as f:
        m = json.load(f)
    if m.get('format') != 1 or m.get('phase') not in PHASES or not isinstance(m.get('files'), dict):
        raise PatchError('%s: not an overlay manifest (format 1, phase, files)' % folder)
    for path, meta in list(m['files'].items()) + list(m.get('dirs', {}).items()):
        if path.startswith('/') or '..' in path.split('/'):
            raise PatchError('%s: bad path %s' % (folder, path))
        flag_bits(meta.get('flags', []))
    for path, meta in m['files'].items():
        src = folder / 'files' / path
        if not src.is_file():
            raise PatchError('%s: %s is missing from files/' % (folder, path))
        if sha256_file(src) != meta['sha256']:
            raise PatchError('%s: files/%s does not match its manifest sha256' % (folder, path))
    m['folder'] = str(folder)
    return m


class Attributes:
    """Reads and sets install attributes under ROOT. PRIVILEGED (default: running as root) decides whether owners and
    system flags are set; CHOWN and CHFLAGS can be replaced (tests record the calls instead of needing root)."""

    def __init__(self, root, privileged=None, chown=None, chflags=None):
        self.root = Path(root)
        self.privileged = (os.geteuid() == 0) if privileged is None else privileged
        self.chown = chown or (lambda p, u, g: os.chown(p, u, g, follow_symlinks=False))
        self.chflags = chflags or (lambda p, bits: os.chflags(p, bits, follow_symlinks=False))
        self._ids = {}

    def _table(self, name):
        """{name: id} from ROOT's own passwd/group file, when it has one."""
        if name not in self._ids:
            table = {}
            for rel in ('private/etc/' + name, 'etc/' + name):
                f = self.root / rel
                if f.is_file() and not f.is_symlink():
                    for line in f.read_text(errors='replace').splitlines():
                        parts = line.split(':')
                        if len(parts) > 2 and not line.startswith('#') and parts[2].isdigit():
                            table.setdefault(parts[0], int(parts[2]))
                    break
            self._ids[name] = table
        return self._ids[name]

    def ids(self, owner):
        """(uid, gid) of OWNER ("user:group" by name or number)."""
        user, _, group = (owner or DEFAULT_OWNER).partition(':')
        group = group or user
        import grp
        import pwd
        if user.isdigit():
            uid = int(user)
        elif user in self._table('passwd'):
            uid = self._table('passwd')[user]
        else:
            try:
                uid = pwd.getpwnam(user).pw_uid
            except KeyError:
                raise PatchError('unknown user %s' % user) from None
        if group.isdigit():
            gid = int(group)
        elif group in self._table('group'):
            gid = self._table('group')[group]
        else:
            try:
                gid = grp.getgrnam(group).gr_gid
            except KeyError:
                raise PatchError('unknown group %s' % group) from None
        return uid, gid

    def wanted(self, meta, default_mode='0644'):
        uid, gid = self.ids(meta.get('owner'))
        return {'mode': int(meta.get('mode') or default_mode, 8), 'uid': uid, 'gid': gid,
                'flags': flag_bits(meta.get('flags', []))}

    @staticmethod
    def current(path):
        st = os.lstat(path)
        return {'mode': stat.S_IMODE(st.st_mode), 'uid': st.st_uid, 'gid': st.st_gid,
                'flags': getattr(st, 'st_flags', 0) & (SYSTEM_FLAGS | 0xffff)}

    def settable(self, key, want, have):
        """Whether this run can make HAVE[KEY] what WANT says (owners and system flags need root)."""
        if key in ('uid', 'gid'):
            return self.privileged
        if key == 'flags':
            return self.privileged or not ((want ^ have) & SYSTEM_FLAGS)
        return True

    def differs(self, path, want):
        """Keys of WANT that PATH does not have: (settable, not settable by this run)."""
        have = self.current(path)
        keys = [k for k in ('mode', 'uid', 'gid', 'flags') if have[k] != want[k]]
        return [k for k in keys if self.settable(k, want[k], have[k])], \
               [k for k in keys if not self.settable(k, want[k], have[k])]

    def clear_flags(self, path):
        """Remove the flags that would stop PATH being replaced (as the hooks' chflags noschg,nouchg,norestricted)."""
        have = self.current(path)['flags']
        if have and (self.privileged or not have & SYSTEM_FLAGS):
            self.chflags(path, 0)

    def apply(self, path, want):
        """Set WANT on PATH as far as this run can; returns the keys it could not set."""
        have = self.current(path)
        if have['flags'] and (self.privileged or not have['flags'] & SYSTEM_FLAGS):
            self.chflags(path, 0)                     # an immutable file's mode and owner cannot change
        os.chmod(path, want['mode'])
        skipped = []
        if self.privileged:
            self.chown(path, want['uid'], want['gid'])
        elif (have['uid'], have['gid']) != (want['uid'], want['gid']):
            skipped += ['uid', 'gid']
        if want['flags'] & SYSTEM_FLAGS and not self.privileged:
            skipped.append('flags')
            if want['flags'] & ~SYSTEM_FLAGS:
                self.chflags(path, want['flags'] & ~SYSTEM_FLAGS)
        elif want['flags']:
            self.chflags(path, want['flags'])
        return skipped


def plan(overlays, root, mode='live', attributes=None):
    """{'write': [...], 'attrs': [...], 'same': [...], 'needs_recovery': [packs]}: files whose content differs,
    files whose content is right but whose mode, owner or flags are not, and files that need nothing. In live mode
    installer-phase packs are not applied (they touch kexts, the cryptex or the EFI partition) and are listed
    instead."""
    root = Path(root)
    attributes = attributes or Attributes(root)
    out = {'write': [], 'attrs': [], 'same': [], 'needs_recovery': [], 'dirs': {}}
    seen = {}
    for m in overlays:
        if mode == 'live' and m['phase'] == 'installer':
            out['needs_recovery'].append(m['pack'])
            continue
        for d, meta in m.get('dirs', {}).items():
            out['dirs'].setdefault(d, meta)
        for path, meta in sorted(m['files'].items()):
            if path in seen:
                raise PatchError('%s is in packs %s and %s' % (path, seen[path], m['pack']))
            seen[path] = m['pack']
            target = root / path
            old = sha256_file(target) if target.is_file() and not target.is_symlink() else None
            row = {'path': path, 'pack': m['pack'], 'source': str(Path(m['folder']) / 'files' / path),
                   'sha256': meta['sha256'], 'old_sha256': old, 'mode': meta.get('mode'),
                   'owner': meta.get('owner'), 'flags': meta.get('flags', []),
                   'want': attributes.wanted(meta)}
            if old != meta['sha256']:
                out['write'].append(row)
                continue
            fix, cannot = attributes.differs(target, row['want'])
            row['attrs'], row['cannot'] = fix, cannot
            (out['attrs'] if fix else out['same']).append(row)
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


def _folders(root, rel, dirs, attributes, created):
    """Create the missing folders above REL, each with the mode and owner the manifest's DIRS gives it (else
    0755 root:wheel); every folder created is appended to CREATED."""
    parts = Path(rel).parent.parts
    for i in range(len(parts)):
        sub = '/'.join(parts[:i + 1])
        folder = Path(root) / sub
        if folder.is_dir():
            continue
        folder.mkdir()
        created.append(sub)
        meta = dirs.get(sub, {'mode': '0755', 'owner': DEFAULT_OWNER})
        attributes.apply(folder, attributes.wanted(meta, '0755'))


def _install(source, target, want, attributes):
    tmp = target.with_name('.hao-patch-' + target.name)
    shutil.copyfile(source, tmp)
    if target.exists():
        attributes.clear_flags(target)
    os.replace(tmp, target)
    return attributes.apply(target, want)


def _attrs_record(a):
    return {'mode': '%04o' % a['mode'], 'uid': a['uid'], 'gid': a['gid'], 'flags': flag_names(a['flags'])}


def apply(overlays, root, state, *, mode='live', backend=None, on_event=None, now=None, attributes=None):
    """Write every file of PLAN that differs, set attributes that differ, then snapshot. Returns the record."""
    backend = backend or DirectoryBackend()
    emit = on_event or (lambda e: None)
    root, state = Path(root), Path(state)
    attributes = attributes or Attributes(root)
    backend.prepare(root)
    with Lock(state):
        p = plan(overlays, root, mode, attributes)
        emit({'ev': 'plan', 'write': len(p['write']), 'attrs': len(p['attrs']), 'same': len(p['same']),
              'needs_recovery': p['needs_recovery']})
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
                  'needs_recovery': p['needs_recovery'], 'files': [], 'folders': [], 'state': 'writing',
                  'privileged': attributes.privileged}
        _save(folder, record)
        for row in p['write']:
            target = root / row['path']
            now_sha = sha256_file(target) if target.is_file() and not target.is_symlink() else None
            if now_sha != row['old_sha256']:
                raise PatchError('%s changed while patching' % row['path'])
            before = None
            if now_sha is not None:
                before = _attrs_record(attributes.current(target))
                backup = folder / 'backup' / row['path']
                backup.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(target, backup)
            _folders(root, row['path'], p['dirs'], attributes, record['folders'])
            skipped = _install(row['source'], target, row['want'], attributes)
            after = sha256_file(target)
            if after != row['sha256']:
                raise PatchError('%s: wrote %s, expected %s' % (row['path'], after, row['sha256']))
            record['files'].append(dict({k: row[k] for k in ('path', 'pack', 'sha256', 'old_sha256')},
                                        before=before, after=_attrs_record(attributes.current(target)),
                                        not_set=skipped))
            _save(folder, record)
            emit({'ev': 'file.write', 'path': row['path'], 'pack': row['pack'], 'new': now_sha is None,
                  'sha256': row['sha256'], 'old_sha256': now_sha})
        for row in p['attrs']:
            target = root / row['path']
            if sha256_file(target) != row['sha256']:
                raise PatchError('%s changed while patching' % row['path'])
            before = _attrs_record(attributes.current(target))
            skipped = attributes.apply(target, row['want'])
            record['files'].append(dict({k: row[k] for k in ('path', 'pack', 'sha256')}, old_sha256=row['sha256'],
                                        attrs_only=True, before=before,
                                        after=_attrs_record(attributes.current(target)),
                                        not_set=sorted(set(skipped + row['cannot']))))
            _save(folder, record)
            emit({'ev': 'file.attrs', 'path': row['path'], 'pack': row['pack'], 'keys': row['attrs']})
        record['snapshot'] = backend.snapshot(root, patch_id)
        record['state'] = 'applied'
        _save(folder, record)
        emit({'ev': 'done', 'id': patch_id, 'written': sum(1 for f in record['files'] if not f.get('attrs_only')),
              'attrs': sum(1 for f in record['files'] if f.get('attrs_only')), 'same': len(p['same']),
              'not_set': sum(1 for f in record['files'] if f.get('not_set')),
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


def _restore_attrs(target, before, attributes):
    want = {'mode': int(before['mode'], 8), 'uid': before['uid'], 'gid': before['gid'],
            'flags': flag_bits(before['flags'])}
    attributes.apply(target, want)


def revert(root, state, *, patch_id=None, backend=None, on_event=None, attributes=None):
    """Undo the newest applied patch (or PATCH_ID): restore replaced files and their attributes, remove added files
    and folders, check each."""
    backend = backend or DirectoryBackend()
    emit = on_event or (lambda e: None)
    root, state = Path(root), Path(state)
    attributes = attributes or Attributes(root)
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
                if target.exists():
                    attributes.clear_flags(target)
                target.unlink(missing_ok=True)
            elif not f.get('attrs_only'):
                backup = folder / 'backup' / f['path']
                tmp = target.with_name('.hao-revert-' + target.name)
                shutil.copyfile(backup, tmp)
                attributes.clear_flags(target)
                os.replace(tmp, target)
                if sha256_file(target) != f['old_sha256']:
                    raise PatchError('%s: restored file does not match its recorded sha256' % f['path'])
            if f['old_sha256'] is not None and f.get('before'):
                _restore_attrs(target, f['before'], attributes)
            emit({'ev': 'file.revert', 'path': f['path'], 'removed': f['old_sha256'] is None,
                  'attrs_only': bool(f.get('attrs_only'))})
        for sub in reversed(record.get('folders', [])):
            try:
                (root / sub).rmdir()                   # only when empty: something else may have been put there
            except OSError:
                pass
        backend.revert(root, record)
        record['state'] = 'reverted'
        _save(folder, record)
        emit({'ev': 'reverted', 'id': record['id'], 'files': len(record['files'])})
        return record
