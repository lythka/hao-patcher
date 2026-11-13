"""Apply pack overlays to a system root: only what differs, checked before and after, revertible.

An overlay is one pack's materialised files: OVERLAY/manifest.json + OVERLAY/files/<path>.

  {"format": 1, "pack": NAME, "phase": "installer" | "live-capable",
   "files": {PATH: {"sha256": HEX, "mode": "0755", "owner": "root:wheel", "flags": ["restricted"]}},
   "dirs": {FOLDER: {"mode": "0700", "owner": "248:248"}}}

A file is written when its content differs, and only its attributes are set when the content is the same but its
mode, owner or flags are not the manifest's. "owner" is USER:GROUP by name (looked up in ROOT's own
private/etc/passwd and group first, then this system's) or by number (UID:GID, as `tar --numeric-owner` keeps
them). "flags" are chflags names (FLAG_BITS). "dirs" lists folders the pack installs (a tar's folder entries, empty
ones included): hao creates each that is missing, parents first, with its mode and owner, and its "mtime" when it
has one once the files inside it are written; a folder above a file that "dirs" does not list is 0755 root:wheel.

Owners and system flags (restricted, schg, sunlnk) can only be set by root. Without root, hao sets the mode and the
user flags, and records per file what it could not set ("not_set"; the done event counts them) rather than
failing: that is how the fixture tests run, and why a non-root run is never a proof of attributes.

In live mode nothing of an installer-phase pack is written, nor any file of a live-capable pack that only the
installer can make take effect (Classifier: kernel collection members, dyld shared cache images unless dyld loads
on-disk roots there, the cryptex, the booter, the EFI partition); both are listed as needing Recovery.

State lives in STATE (on the target: /private/var/db/hao): a lock, and one folder per applied patch holding its
record (every file's sha256 and attributes before and after, which files and folders were new) and a backup of
every replaced file, so revert restores replaced files and their attributes and removes files and folders the patch
added. Each file is entered in the record before it is written, so a failure part way undoes every write of the
patch (state 'rolled-back'). A backend says where files are read and written and snapshots around the writes:
DirectoryBackend (a plain folder root, tests) does neither; macos.MacOSLiveBackend writes the booted Mac's System
volume and boots it as a new snapshot.
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


# Paths no live patch writes, whatever pack they are in: what reads them is made on the installer (the kernel
# collections, the dyld shared cache, the cryptex, the booter in Preboot, the EFI partition). Structural, not
# version-specific: these are where macOS keeps those things on every release hao supports.
INSTALLER_PATHS = (
    ('System/Library/Extensions/', 'a kernel collection member (the installer links the kernel collections)'),
    ('Library/Extensions/', 'a kernel collection member (the installer links the auxiliary kernel collection)'),
    ('System/Library/KernelCollections/', 'a kernel collection (linked on the installer)'),
    ('System/Library/Kernels/', 'the kernel (linked into the kernel collections on the installer)'),
    ('System/Library/PrelinkedKernels/', 'a prelinked kernel (made on the installer)'),
    ('System/Library/dyld/', 'the dyld shared cache (made on the installer)'),
    ('System/Library/Caches/com.apple.dyld/', 'the dyld shared cache (made on the installer)'),
    ('System/Cryptexes/', 'in the cryptex (sealed on the installer)'),
    ('System/Volumes/', 'on another volume (Preboot, the cryptex: written by the installer)'),
    ('private/preboot/', 'on the Preboot volume (written by the installer)'),
    ('System/Library/CoreServices/boot.efi', 'the booter (bless copies it to Preboot; changed on the installer)'),
    ('usr/standalone/', 'a booter file (changed on the installer)'),
    ('EFI/', 'on the EFI partition (written by the installer)'),
)
# Where dyld's cache maps are (OCLP-era Intel layouts and the macOS 13+ cryptex): every line of a .map that starts
# with / is an image the cache holds, and dyld maps the cache's copy of it, not the file at its path.
CACHE_MAP_DIRS = ('System/Volumes/Preboot/Cryptexes/OS/System/Library/dyld', 'System/Cryptexes/OS/System/Library/dyld',
                  'System/Library/dyld')
CACHE_REASON = 'in the dyld shared cache (dyld maps the cache\'s copy; the installer rebuilds the cache)'
CSR_ALLOW_APPLE_INTERNAL = 0x10


class Classifier:
    """Why a path may not be written live (None when it may): INSTALLER_PATHS, and membership of the dyld shared
    cache as the .map files beside the caches under BOOTED (the running system's root) list it.

    CACHE_ROOTS: dyld on this system loads a file at a cache image's path instead of the cache's copy (an on-disk
    root). macOS dyld looks for such roots only when csr_check(CSR_ALLOW_APPLE_INTERNAL) succeeds, which hao's images
    arrange (IntelCommpage's ic_csr_internal=1; it is how OCLP's Metal wrapper and MetalOld replace 27's Metal). Then
    a cache image is written live like any other file: each process loads the new file when it next starts."""

    def __init__(self, booted=None, cache_roots=False):
        self.cached = set()
        self.maps = []
        self.cache_roots = bool(cache_roots)
        for d in CACHE_MAP_DIRS if booted is not None else ():
            folder = Path(booted) / d
            for m in sorted(folder.glob('*.map')) if folder.is_dir() else ():
                self.maps.append(str(m))
                for line in m.read_text(errors='replace').splitlines():
                    if line.startswith('/'):
                        self.cached.add(line.strip().lstrip('/'))

    def __call__(self, path):
        for prefix, reason in INSTALLER_PATHS:
            if path == prefix.rstrip('/') or path.startswith(prefix):
                return reason
        if path in self.cached and not self.cache_roots:
            return CACHE_REASON
        return None


def plan(overlays, root, mode='live', attributes=None, resolve=None, classify=None):
    """{'write': [...], 'attrs': [...], 'same': [...], 'needs_recovery': [packs], 'needs_recovery_files': [...]}:
    files whose content differs, files whose content is right but whose mode, owner or flags are not, and files that
    need nothing. In live mode installer-phase packs are not applied (they touch kexts, the cryptex or the EFI
    partition) and are listed instead, and so is every file CLASSIFY (live mode only) gives a reason for: a kernel
    collection member or dyld cache image in a live-capable pack. RESOLVE maps a path to the file to compare
    (default ROOT/path)."""
    root = Path(root)
    attributes = attributes or Attributes(root)
    resolve = resolve or (lambda p: root / p)
    out = {'write': [], 'attrs': [], 'same': [], 'needs_recovery': [], 'needs_recovery_files': [], 'dirs': {}}
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
            why = classify(path) if classify and mode == 'live' else None
            if why:
                out['needs_recovery_files'].append({'path': path, 'pack': m['pack'], 'reason': why})
                continue
            target = resolve(path)
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
    out['mkdirs'] = [d for d in sorted(out['dirs']) if not resolve(d).is_dir()
                     and not (classify and mode == 'live' and classify(d))]
    return out


def plan_event(p):
    return {'ev': 'plan', 'write': len(p['write']), 'attrs': len(p['attrs']), 'same': len(p['same']),
            'folders': len(p['mkdirs']),
            'needs_recovery': p['needs_recovery'], 'needs_recovery_files': p['needs_recovery_files']}


class DirectoryBackend:
    """No snapshots: the root is a plain folder (tests, or a volume the operator mounted read-write). Every hook of
    the live backend exists here as a no-op, so apply and revert run the same steps for both."""
    name = 'directory'
    record_noop = True              # a patch that changes nothing still gets a record (and an id)
    separate_write_volume = False   # booted_path and path are the same file

    def __init__(self):
        self.emit = lambda e: None

    def prepare(self, root):
        if not Path(root).is_dir():
            raise PatchError('%s is not a folder' % root)
        self.root = Path(root)

    def attributes(self, root):
        return Attributes(root)

    def classifier(self, root):
        return Classifier(root)

    def booted_path(self, rel):
        """The file as the running system sees it (what the plan compares)."""
        return self.root / rel

    def path(self, rel):
        """The file hao writes."""
        return self.root / rel

    def check_records(self, recs):
        pass

    def describe(self):
        return {}

    def open_write(self):
        pass

    def close_write(self):
        pass

    def before_write(self, patch_id):
        return None

    def snapshot(self, root, name):
        return None

    def choose_revert(self, applied, patch_id):
        if patch_id:
            applied = [r for r in applied if r['id'] == patch_id]
        if not applied:
            raise PatchError('nothing to revert%s' % (' named ' + patch_id if patch_id else ''))
        return applied[-1]

    def revert(self, root, record):
        return None

    def after_apply(self, record):
        return {}

    def after_revert(self, record):
        return {}


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


def _folders(resolve, rel, dirs, attributes, created, itself=False):
    """Create the missing folders above REL (and REL itself when ITSELF), parents first, each with the mode and
    owner the manifest's DIRS gives it (else 0755 root:wheel); every folder created is appended to CREATED."""
    parts = Path(rel).parts if itself else Path(rel).parent.parts
    for i in range(len(parts)):
        sub = '/'.join(parts[:i + 1])
        folder = resolve(sub)
        if folder.is_dir():
            continue
        folder.mkdir()
        created.append(sub)
        meta = dirs.get(sub, {'mode': '0755', 'owner': DEFAULT_OWNER})
        attributes.apply(folder, attributes.wanted(meta, '0755'))


def _folder_times(resolve, created, dirs):
    """Give each folder of CREATED the mtime DIRS dates it with (a tar folder's, as `tar -x` leaves it), deepest
    first, once nothing more is written inside it."""
    for sub in sorted(set(created), key=lambda s: (-s.count('/'), s)):
        mtime = dirs.get(sub, {}).get('mtime')
        if mtime is not None:
            os.utime(resolve(sub), (mtime, mtime), follow_symlinks=False)


def _install(source, target, want, attributes):
    tmp = target.with_name('.hao-patch-' + target.name)
    shutil.copyfile(source, tmp)
    if target.exists():
        attributes.clear_flags(target)
    os.replace(tmp, target)
    return attributes.apply(target, want)


def _attrs_record(a):
    return {'mode': '%04o' % a['mode'], 'uid': a['uid'], 'gid': a['gid'], 'flags': flag_names(a['flags'])}


def deferred_report(entries, phases, classify, written):
    """Each deferred.json entry (in its order, which already puts native-identities after the overlays) with what
    happens to it here: 'needs-recovery' (its pack is installer-phase or unknown, or its path is installer-only) or
    'not-run' (target-side and live-capable, but hao has no live runner for it yet). 'stale' marks an entry made
    after the overlays (keyed on the installed files) when this patch wrote files: it no longer matches them."""
    out = []
    for e in entries:
        why = classify(e['path']) if classify else None
        phase = phases.get(e.get('pack'))
        if phase != 'live-capable' or why:
            status, reason = 'needs-recovery', why or ('pack %s is %s' % (e.get('pack'), phase or 'not given'))
        else:
            status, reason = 'not-run', 'no live runner for it yet: %s' % e.get('reason', '')
        out.append({'ev': 'deferred', 'path': e['path'], 'pack': e.get('pack'), 'status': status, 'reason': reason,
                    'after': e.get('after', []), 'stale': bool(written and 'overlays' in e.get('after', []))})
    return out


def apply(overlays, root, state, *, mode='live', backend=None, on_event=None, now=None, attributes=None,
          deferred=None):
    """Write every file of PLAN that differs, set attributes that differ, then snapshot. Returns the record (None
    when the backend keeps no record of a patch that changes nothing). A failure after the first write undoes every
    write of this patch (record state 'rolled-back') and raises."""
    backend = backend or DirectoryBackend()
    emit = on_event or (lambda e: None)
    backend.emit = emit
    root, state = Path(root), Path(state)
    backend.prepare(root)
    attributes = attributes or backend.attributes(root)
    classify = backend.classifier(root) if mode == 'live' else None
    phases = {m['pack']: m['phase'] for m in overlays}
    with Lock(state):
        backend.check_records(records(state))
        p = plan(overlays, root, mode, attributes, resolve=backend.booted_path, classify=classify)
        emit(plan_event(p))
        if not (p['write'] or p['attrs'] or p['mkdirs']) and not backend.record_noop:
            for ev in deferred_report(deferred or [], phases, classify, False):
                emit(ev)
            emit({'ev': 'done', 'id': None, 'written': 0, 'attrs': 0, 'same': len(p['same']), 'not_set': 0,
                  'snapshot': None, 'needs_recovery': p['needs_recovery'], 'reboot': False})
            return None
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
                  'needs_recovery': p['needs_recovery'], 'needs_recovery_files': p['needs_recovery_files'],
                  'files': [], 'folders': [], 'state': 'writing', 'privileged': attributes.privileged}
        record.update(backend.describe())
        _save(folder, record)
        try:
            backend.open_write()
            if backend.separate_write_volume:
                _same_plan(p, plan(overlays, root, mode, attributes, resolve=backend.path, classify=classify))
            record['snapshot_pre'] = backend.before_write(patch_id)
            _save(folder, record)
            _write(p, record, folder, backend.path, attributes, emit)
            record['snapshot'] = backend.snapshot(root, patch_id)
        except BaseException as x:
            try:
                _undo(record, folder, backend.path, attributes, emit)
            except Exception as y:
                record['state'] = 'rollback-failed'
                record['error'] = '%s; rollback failed: %s' % (x, y)
                _save(folder, record)
                backend.close_write()
                raise PatchError('%s; rolling back failed too: %s (record %s)' % (x, y, folder)) from x
            record['state'] = 'rolled-back'
            record['error'] = str(x)
            _save(folder, record)
            emit({'ev': 'rolled-back', 'id': patch_id, 'files': len(record['files']), 'error': str(x)})
            backend.close_write()
            raise
        backend.close_write()
        record['state'] = 'applied'
        record.update(backend.after_apply(record))
        _save(folder, record)
        written = [f for f in record['files'] if not f.get('attrs_only')]
        for ev in deferred_report(deferred or [], phases, classify, bool(written)):
            emit(ev)
        emit({'ev': 'done', 'id': patch_id, 'written': len(written),
              'attrs': sum(1 for f in record['files'] if f.get('attrs_only')), 'same': len(p['same']),
              'not_set': sum(1 for f in record['files'] if f.get('not_set')),
              'snapshot': record['snapshot'], 'needs_recovery': p['needs_recovery'],
              'reboot': bool(record.get('reboot'))})
        return record


def _same_plan(booted, live):
    """The plan made from the running system and the one made from the volume hao writes must agree file by file:
    a difference means something wrote the volume without making a snapshot of it (or a patch is waiting for a
    reboot), and hao would record the wrong 'before'."""
    a = {r['path']: r['old_sha256'] for r in booted['write'] + booted['attrs'] + booted['same']}
    b = {r['path']: r['old_sha256'] for r in live['write'] + live['attrs'] + live['same']}
    for path in sorted(set(a) | set(b)):
        if a.get(path) != b.get(path):
            raise PatchError('%s differs between the running system and its System volume (written since boot '
                             'without a snapshot?): not patching' % path)


def _write(p, record, folder, resolve, attributes, emit):
    for d in p['mkdirs']:                           # every missing folder of the manifests' "dirs", files or not
        _folders(resolve, d, p['dirs'], attributes, record['folders'], itself=True)
        _save(folder, record)
    for row in p['write']:
        target = resolve(row['path'])
        now_sha = sha256_file(target) if target.is_file() and not target.is_symlink() else None
        if now_sha != row['old_sha256']:
            raise PatchError('%s changed while patching' % row['path'])
        before = None
        if now_sha is not None:
            before = _attrs_record(attributes.current(target))
            backup = folder / 'backup' / row['path']
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(target, backup)
            if sha256_file(backup) != now_sha:
                raise PatchError('%s: the backup does not match the file' % row['path'])
        entry = dict({k: row[k] for k in ('path', 'pack', 'sha256', 'old_sha256')}, before=before, pending=True)
        record['files'].append(entry)               # recorded before the write, so a rollback undoes it too
        _save(folder, record)
        _folders(resolve, row['path'], p['dirs'], attributes, record['folders'])
        skipped = _install(row['source'], target, row['want'], attributes)
        after = sha256_file(target)
        if after != row['sha256']:
            raise PatchError('%s: wrote %s, expected %s' % (row['path'], after, row['sha256']))
        entry.pop('pending')
        entry.update(after=_attrs_record(attributes.current(target)), not_set=skipped)
        _save(folder, record)
        emit({'ev': 'file.write', 'path': row['path'], 'pack': row['pack'], 'new': now_sha is None,
              'sha256': row['sha256'], 'old_sha256': now_sha})
    for row in p['attrs']:
        target = resolve(row['path'])
        if sha256_file(target) != row['sha256']:
            raise PatchError('%s changed while patching' % row['path'])
        entry = dict({k: row[k] for k in ('path', 'pack', 'sha256')}, old_sha256=row['sha256'], attrs_only=True,
                     before=_attrs_record(attributes.current(target)), pending=True)
        record['files'].append(entry)
        _save(folder, record)
        skipped = attributes.apply(target, row['want'])
        entry.pop('pending')
        entry.update(after=_attrs_record(attributes.current(target)), not_set=sorted(set(skipped + row['cannot'])))
        _save(folder, record)
        emit({'ev': 'file.attrs', 'path': row['path'], 'pack': row['pack'], 'keys': row['attrs']})
    _folder_times(resolve, record['folders'], p['dirs'])


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


def _undo(record, folder, resolve, attributes, emit):
    """Put every file of RECORD back (newest first): replaced files from the backup, checked against their recorded
    sha256, with their attributes; new files removed; then the folders the patch made, when empty."""
    for f in reversed(record['files']):
        target = resolve(f['path'])
        target.with_name('.hao-patch-' + target.name).unlink(missing_ok=True)    # a copy cut short
        if f['old_sha256'] is None:
            if target.exists() or target.is_symlink():
                attributes.clear_flags(target)
            target.unlink(missing_ok=True)
        elif not f.get('attrs_only'):
            backup = folder / 'backup' / f['path']
            tmp = target.with_name('.hao-revert-' + target.name)
            shutil.copyfile(backup, tmp)
            if target.exists():
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
            resolve(sub).rmdir()                   # only when empty: something else may have been put there
        except OSError:
            pass


def revert(root, state, *, patch_id=None, backend=None, on_event=None, attributes=None):
    """Undo the newest applied patch (or PATCH_ID): restore replaced files and their attributes, remove added files
    and folders, check each; then the backend boots the system as it was (the live backend: the snapshot that was
    booted before the patch)."""
    backend = backend or DirectoryBackend()
    emit = on_event or (lambda e: None)
    backend.emit = emit
    root, state = Path(root), Path(state)
    backend.prepare(root)
    attributes = attributes or backend.attributes(root)
    with Lock(state):
        record = backend.choose_revert([r for r in records(state) if r['state'] == 'applied'], patch_id)
        folder = state / 'patches' / record['id']
        backend.open_write()
        try:
            for f in reversed(record['files']):
                target = backend.path(f['path'])     # as the patch left it, or already put back (a revert re-run)
                if target.is_file() and sha256_file(target) not in (f['sha256'], f['old_sha256']):
                    raise PatchError('%s changed after %s: not reverting it' % (f['path'], record['id']))
            _undo(record, folder, backend.path, attributes, emit)
            record['snapshot_reverted_to'] = backend.revert(root, record)
        finally:
            backend.close_write()
        record['state'] = 'reverted'
        record.update(backend.after_revert(record))
        _save(folder, record)
        emit({'ev': 'reverted', 'id': record['id'], 'files': len(record['files']),
              'snapshot': record.get('snapshot_reverted_to'), 'reboot': bool(record.get('reboot'))})
        return record
