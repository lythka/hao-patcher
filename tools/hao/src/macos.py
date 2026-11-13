"""The live macOS backend of hao patch: write the booted Mac's own System volume, boot it as a new snapshot, and
revert by making the snapshot that was booted before the patch the boot snapshot again.

On an Intel Mac that boots macOS 11 or later with SIP's filesystem protection and authenticated root off
(csr-active-config with CSR_ALLOW_UNRESTRICTED_FS 0x2 and CSR_ALLOW_UNAUTHENTICATED_ROOT 0x800, as OpenCore sets it),
`/` is a read-only snapshot of the System volume. A patch:

  1. finds the System and Data volumes from `diskutil info -plist /` (the booted snapshot's UUID, container and
     volume group), `diskutil apfs list -plist CONTAINER` (volume roles) and `diskutil apfs listSnapshots -plist DEV`
     (the System volume is the one of the group whose snapshots hold the booted one): never from the device name;
  2. refuses unless it runs as root on x86_64, SIP allows both, nothing else has the System volume mounted, and no
     hao patch or revert happened since this boot (the volume would not be what is running);
  3. plans against the running system, mounts the System volume read-write (`mount -o nobrowse -t apfs`) at
     MOUNT_POINT, checks the plan again against it, and takes a named snapshot of it
     (`apfs_systemsnapshot -s hao-pre-ID -v MNT`);
  4. writes the files (System paths into MNT; paths under a firmlink of /usr/share/firmlinks, such as private/ or
     Library/, into the Data volume, which is live: those take effect at once);
  5. `bless --mount MNT --create-snapshot` (as the image build makes its snapshot; `--bless oclp` gives OCLP's Intel
     form, `--folder MNT/System/Library/CoreServices --bootefi --create-snapshot`), records the new snapshot's name
     (the one listSnapshots did not list before), unmounts, and reports what must restart.

Revert restores the files (the volume then equals what was booted before) and tags the snapshot that was booted
before the patch as the one to root from (`apfs_systemsnapshot -r NAME -v MNT`; else the hao-pre snapshot; else a new
bless snapshot of the restored volume). `bless --last-sealed-snapshot` is no option: an image built by hao has no
sealed snapshot. Every command goes through a Host, so tests answer them from fixtures and keep the sequence.
"""
import ctypes
import os
import platform
import plistlib
import re
import subprocess
from pathlib import Path

import livepatch as LP

DISKUTIL = '/usr/sbin/diskutil'
MOUNT = '/sbin/mount'
UMOUNT = '/sbin/umount'
BLESS = '/usr/sbin/bless'
APFS_SYSTEMSNAPSHOT = '/System/Library/Filesystems/apfs.fs/Contents/Resources/apfs_systemsnapshot'
LSOF = '/usr/sbin/lsof'
SYSCTL = '/usr/sbin/sysctl'
CSRUTIL = '/usr/bin/csrutil'
STATE = '/private/var/db/hao'
MOUNT_POINT = '/private/var/run/hao-live-system'    # cleared at boot; not softwareupdate's Update/mnt1
DATA_PREFIX = '/System/Volumes/Data'                 # how the kernel may report a mount point under /private
CSR_ALLOW_UNRESTRICTED_FS = 0x2
CSR_ALLOW_UNAUTHENTICATED_ROOT = 0x800
PRE = 'hao-pre-'
# Default 'oclp': on Intel, bless refuses snapshot options without --bootefi (laptop 2026-11-12: "Can't use snapshot
# options without --bootefi").
BLESS_FORMS = {
    'mount': lambda mnt: [BLESS, '--mount', mnt, '--create-snapshot'],
    'oclp': lambda mnt: [BLESS, '--folder', mnt + '/System/Library/CoreServices', '--bootefi', '--create-snapshot'],
}
LAUNCHD = (('System/Library/LaunchDaemons', 'system'), ('Library/LaunchDaemons', 'system'),
           ('System/Library/LaunchAgents', 'gui'), ('Library/LaunchAgents', 'gui'))
NOT_PROVEN = ('the live macOS backend is not proven yet (workflow plan step 9a laptop run): it runs only with '
              '--i-have-a-backup')


class Host:
    """What the live backend asks of the running Mac."""

    def run(self, argv, check=True, timeout=120):
        """CompletedProcess(args, returncode, stdout bytes, stderr bytes); PatchError when CHECK and it fails."""
        raise NotImplementedError

    def csr_config(self):
        """csr_get_active_config(), or None when it cannot be read."""
        raise NotImplementedError

    def euid(self):
        raise NotImplementedError

    def machine(self):
        raise NotImplementedError

    def fs(self, path):
        """The local Path of PATH, an absolute path of the running system."""
        raise NotImplementedError


class SystemHost(Host):
    def run(self, argv, check=True, timeout=120):
        argv = [str(a) for a in argv]
        try:
            r = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               timeout=timeout)
        except subprocess.TimeoutExpired:
            raise LP.PatchError('%s did not finish in %d s' % (' '.join(argv), timeout)) from None
        except OSError as x:
            raise LP.PatchError('%s: %s' % (argv[0], x)) from None
        if check and r.returncode:
            raise LP.PatchError('%s failed (%d): %s' % (' '.join(argv), r.returncode, _text(r)))
        return r

    def csr_config(self):
        try:
            lib = ctypes.CDLL('/usr/lib/libSystem.B.dylib')
            value = ctypes.c_uint32(0)
            return value.value if lib.csr_get_active_config(ctypes.byref(value)) == 0 else None
        except (OSError, AttributeError):
            return None

    def euid(self):
        return os.geteuid()

    def machine(self):
        return platform.machine()

    def fs(self, path):
        return Path(path)


def _text(r):
    return (r.stderr or r.stdout or b'').decode(errors='replace').strip()


def _plist(host, *argv):
    r = host.run(argv)
    try:
        return plistlib.loads(r.stdout)
    except Exception:
        raise LP.PatchError('%s: output is not a plist' % ' '.join(argv)) from None


def mounts(host):
    """[(source, mount point, [options])] as mount(8) lists them."""
    out = []
    for line in host.run([MOUNT]).stdout.decode(errors='replace').splitlines():
        source, sep, rest = line.partition(' on ')
        if sep:
            point, _, options = rest.rpartition(' (')
            out.append((source, point, [o.strip() for o in options.rstrip(')').split(',')]))
    return out


def snapshots(host, device, mount=None):
    """The snapshots of the APFS volume DEVICE. diskutil only lists them for a volume with a mount point (laptop,
    2026-11-12: `listSnapshots disk1s2` fails with -69854 "A disk with a mount point is required" for the booted
    System volume, whose snapshot, not the volume, is mounted at /), so MOUNT (e.g. "/") is asked when DEVICE fails."""
    try:
        listing = _plist(host, DISKUTIL, 'apfs', 'listSnapshots', '-plist', device)
    except Exception:
        if not mount:
            raise
        listing = _plist(host, DISKUTIL, 'apfs', 'listSnapshots', '-plist', mount)
    return [{'name': s.get('SnapshotName'), 'uuid': s.get('SnapshotUUID'), 'xid': s.get('SnapshotXID'),
             'root_to': s.get('RootTo'), 'revert_to': s.get('RevertTo'), 'purgeable': s.get('Purgeable')}
            for s in listing.get('Snapshots', [])]


def boot_time(host):
    r = host.run([SYSCTL, '-n', 'kern.boottime'], check=False)
    m = re.search(r'sec = (\d+)', r.stdout.decode(errors='replace'))
    return int(m.group(1)) if m else None


def discover(host):
    """The booted snapshot, the System and Data volumes of its volume group, the System volume's snapshots and
    where it is mounted. Problems that rule out a live patch are listed in 'problems' (not raised)."""
    root = _plist(host, DISKUTIL, 'info', '-plist', '/')
    f = {'root_device': root.get('DeviceIdentifier'), 'from_snapshot': bool(root.get('APFSSnapshot')),
         'booted': {'name': root.get('APFSSnapshotName'), 'uuid': root.get('APFSSnapshotUUID')},
         'sealed': root.get('Sealed'), 'container': root.get('APFSContainerReference'),
         'group': root.get('APFSVolumeGroupID'), 'system': None, 'data': None, 'snapshots': [],
         'system_mounts': [], 'problems': []}
    if root.get('FilesystemType') != 'apfs' or not f['container']:
        f['problems'].append('/ is not on an APFS volume')
        return f
    if not f['from_snapshot'] or not f['booted']['uuid']:
        f['problems'].append('/ (%s) is not mounted from a snapshot' % f['root_device'])
        return f
    listing = _plist(host, DISKUTIL, 'apfs', 'list', '-plist', f['container'])
    volumes = next((c.get('Volumes', []) for c in listing.get('Containers', [])
                    if c.get('ContainerReference') == f['container']), [])
    systems, datas = [], []
    for v in volumes:
        roles = v.get('Roles', [])
        if 'System' not in roles and 'Data' not in roles:
            continue
        info = _plist(host, DISKUTIL, 'info', '-plist', v['DeviceIdentifier'])
        if f['group'] and info.get('APFSVolumeGroupID') != f['group']:
            continue
        (systems if 'System' in roles else datas).append((v, info))
    found = []
    for v, info in systems:
        try:   # "/" is the booted snapshot of exactly one System volume: only that one may answer through it
            snaps = snapshots(host, v['DeviceIdentifier'], '/' if len(systems) == 1 else None)
        except Exception:
            continue
        if any(s['uuid'] == f['booted']['uuid'] for s in snaps):
            found.append((v, info, snaps))
    if len(found) != 1:
        f['problems'].append('%d System volumes of %s hold the booted snapshot %s (want 1)'
                             % (len(found), f['container'], f['booted']['name']))
        return f
    v, info, snaps = found[0]
    f['system'] = {'device': v['DeviceIdentifier'], 'name': v.get('Name'), 'uuid': v.get('APFSVolumeUUID'),
                   'sealed': info.get('Sealed'), 'filevault': bool(v.get('FileVault'))}
    for s in snaps:
        s['booted'] = s['uuid'] == f['booted']['uuid']
    f['snapshots'] = snaps
    if len(datas) != 1:
        f['problems'].append('%d Data volumes in the volume group %s (want 1)' % (len(datas), f['group']))
    else:
        d, dinfo = datas[0]
        f['data'] = {'device': d['DeviceIdentifier'], 'name': d.get('Name'), 'mount': dinfo.get('MountPoint') or None,
                     'locked': bool(d.get('Locked'))}
        if not f['data']['mount']:
            f['problems'].append('the Data volume %s is not mounted' % d['DeviceIdentifier'])
    table = mounts(host)
    f['system_mounts'] = [{'point': p, 'options': o} for dev, p, o in table if dev == '/dev/' + f['system']['device']]
    f['mount_table'] = table
    return f


def _at(point, mount_point):
    return point in (mount_point, DATA_PREFIX + mount_point)


def write_problems(host, facts, mount_point):
    """Why this run cannot write the live System volume (empty: it can)."""
    out = list(facts['problems'])
    if host.euid() != 0:
        out.append('not root: run it with sudo')
    if host.machine() != 'x86_64':
        out.append('not an Intel Mac (%s): the live backend is for Intel Macs booted through OpenCore' % host.machine())
    csr = host.csr_config()
    if csr is None:
        out.append('could not read the SIP configuration (csr_get_active_config)')
    else:
        if not csr & CSR_ALLOW_UNAUTHENTICATED_ROOT:
            out.append('authenticated root is on (csr 0x%x lacks CSR_ALLOW_UNAUTHENTICATED_ROOT 0x800): a changed '
                       'System volume would not boot' % csr)
        if not csr & CSR_ALLOW_UNRESTRICTED_FS:
            out.append('SIP filesystem protection is on (csr 0x%x lacks CSR_ALLOW_UNRESTRICTED_FS 0x2)' % csr)
    for m in facts.get('system_mounts', []):
        if _at(m['point'], mount_point):
            out.append('the System volume is still mounted at %s (an earlier run): umount %s' % (m['point'], mount_point))
        else:
            out.append('the System volume is mounted at %s by something else (softwareupdate, OCLP, laptop-swap?): '
                       'not writing under it' % m['point'])
    for dev, point, _ in facts.get('mount_table', []):
        if _at(point, mount_point) and facts.get('system') and dev != '/dev/' + facts['system']['device']:
            out.append('%s is mounted at %s' % (dev, point))
    if not host.fs(APFS_SYSTEMSNAPSHOT).exists():
        out.append('%s is missing (named snapshots)' % APFS_SYSTEMSNAPSHOT)
    if facts.get('data') and facts['data']['locked']:
        out.append('the Data volume is locked')
    return out


def firmlinks(host):
    """[(path, path on the Data volume)] from the running system's /usr/share/firmlinks, longest first."""
    f = host.fs('/usr/share/firmlinks')
    if not f.is_file():
        raise LP.PatchError('no /usr/share/firmlinks: cannot tell System paths from Data paths')
    out = []
    for line in f.read_text(errors='replace').splitlines():
        src, _, dst = line.partition('\t')
        if src.startswith('/') and dst:
            out.append((src.strip('/'), dst.strip().strip('/')))
    return sorted(out, key=lambda t: -len(t[0]))


class MacOSLiveBackend(LP.DirectoryBackend):
    name = 'macos-live'
    record_noop = False                 # nothing to write: no mount, no snapshot, no record
    separate_write_volume = True        # the plan is made from / and checked again on the mounted volume

    def __init__(self, host=None, *, acknowledged=False, mount_point=MOUNT_POINT, bless='oclp'):
        super().__init__()
        if bless not in BLESS_FORMS:
            raise LP.PatchError('--bless is one of %s' % ', '.join(BLESS_FORMS))
        self.host = host or SystemHost()
        self.acknowledged = acknowledged
        self.mount_point = mount_point
        self.bless = bless
        self.mounted = False
        self.facts = None
        self.after_info = None
        self.reverted_to = None

    # ---- discovery and refusals (read-only)
    def inspect(self):
        """Discover and return the problems; sets what booted_path needs (also for a dry run)."""
        self.facts = discover(self.host)
        self.boot = boot_time(self.host)
        self.links = firmlinks(self.host)
        self.data_mount = (self.facts.get('data') or {}).get('mount')
        return write_problems(self.host, self.facts, self.mount_point)

    def prepare(self, root):
        if not self.acknowledged:
            raise LP.PatchError(NOT_PROVEN)
        problems = self.inspect()
        if problems:
            raise LP.PatchError('not patching the live system:\n  ' + '\n  '.join(problems))
        self.emit({'ev': 'live', 'system': self.facts['system']['device'], 'data': self.facts['data']['device'],
                   'booted': self.facts['booted']['name'], 'sealed': self.facts['sealed']})

    def attributes(self, root):
        return LP.Attributes(self.host.fs('/'))

    def classifier(self, root):
        csr = self.host.csr_config() or 0
        return LP.Classifier(self.host.fs('/'), cache_roots=bool(csr & LP.CSR_ALLOW_APPLE_INTERNAL)
                             or LP.dyld_honours_roots(self.host.fs('/usr/lib/dyld')))

    def volume(self, rel):
        """('data', path inside the Data volume) for a path under a firmlink, else ('system', REL)."""
        for src, dst in self.links:
            if rel == src or rel.startswith(src + '/'):
                return 'data', dst + rel[len(src):]
        return 'system', rel

    def booted_path(self, rel):
        vol, sub = self.volume(rel)
        return self.host.fs(self.data_mount + '/' + sub if vol == 'data' else '/' + rel)

    def path(self, rel):
        vol, sub = self.volume(rel)
        return self.host.fs(self.data_mount + '/' + sub if vol == 'data' else self.mount_point + '/' + rel)

    def check_records(self, recs):
        """Refuse when the System volume may not be what is running: a live patch or revert since this boot (it
        waits for a reboot), or a running system that is not the snapshot the last live patch made."""
        live = [r for r in recs if r.get('backend') == self.name]
        for r in live:
            if r['state'] == 'applied' and r.get('boot') == self.boot:
                raise LP.PatchError('%s was applied in this boot and is not running yet: reboot first' % r['id'])
            if r['state'] == 'reverted' and r.get('revert_boot') == self.boot:
                raise LP.PatchError('%s was reverted in this boot: reboot first' % r['id'])
        applied = [r for r in live if r['state'] == 'applied']
        if applied and applied[-1].get('snapshot') and applied[-1]['snapshot'] != self.facts['booted']['name']:
            raise LP.PatchError('the running system (%s) is not the snapshot %s made (%s): something else changed '
                                'the boot snapshot; not patching' % (self.facts['booted']['name'], applied[-1]['id'],
                                                                     applied[-1]['snapshot']))

    def describe(self):
        return {'system_device': self.facts['system']['device'], 'data_device': self.facts['data']['device'],
                'container': self.facts['container'], 'group': self.facts['group'], 'boot': self.boot,
                'snapshot_booted': dict(self.facts['booted']), 'mount_point': self.mount_point, 'bless': self.bless}

    # ---- the read-write mount
    def open_write(self):
        dev = '/dev/' + self.facts['system']['device']
        self.host.fs(self.mount_point).mkdir(parents=True, exist_ok=True)
        self.host.run([MOUNT, '-o', 'nobrowse', '-t', 'apfs', dev, self.mount_point])
        self.mounted = True
        try:
            at = [m for m in mounts(self.host) if m[0] == dev and _at(m[1], self.mount_point)]
            if len(at) != 1 or 'read-only' in at[0][2] or 'rdonly' in at[0][2]:
                raise LP.PatchError('%s is not mounted read-write at %s after mount' % (dev, self.mount_point))
            version = 'System/Library/CoreServices/SystemVersion.plist'
            mine = plistlib.loads(self.host.fs(self.mount_point + '/' + version).read_bytes())
            running = plistlib.loads(self.host.fs('/' + version).read_bytes())
            if mine.get('ProductBuildVersion') != running.get('ProductBuildVersion'):
                raise LP.PatchError('the mounted System volume is %s, the running system %s'
                                    % (mine.get('ProductBuildVersion'), running.get('ProductBuildVersion')))
        except Exception:
            self.close_write()
            raise
        self.emit({'ev': 'mount', 'device': dev, 'point': self.mount_point})

    def close_write(self):
        if self.mounted:
            r = self.host.run([UMOUNT, self.mount_point], check=False)
            if r.returncode:
                self.emit({'ev': 'warning', 'text': 'umount %s failed (%s): it goes at the next boot'
                           % (self.mount_point, _text(r))})
            self.mounted = False

    # ---- snapshots
    def _snapshots(self):
        return snapshots(self.host, self.facts['system']['device'], '/')

    def _root_to(self, name):
        """Tag NAME to root from on the next boot; False when the system refuses (apfs_systemsnapshot needs an
        entitlement root may lack, as -s does on the laptop)."""
        r = self.host.run([APFS_SYSTEMSNAPSHOT, '-r', name, '-v', self.mount_point], check=False)
        if r.returncode:
            self.emit({'ev': 'warning', 'text': 'apfs_systemsnapshot -r %s refused: %s' % (name, _text(r).strip())})
            return False
        s = next((s for s in self._snapshots() if s['name'] == name), None)
        if s is not None and s['root_to'] is False:
            self.emit({'ev': 'warning', 'text': 'listSnapshots does not show %s as RootTo after tagging it' % name})
        return True

    def before_write(self, patch_id):
        """A named snapshot before writing, when the system allows it. On the laptop (2026-11-12, 26B131, csr
        0x813) apfs_systemsnapshot -s fails "fs_snapshot_create: Operation not permitted" (it needs an entitlement
        root lacks), so it is best effort: revert then roots to the snapshot booted before, or blesses a new
        snapshot of the restored volume."""
        name = PRE + patch_id
        r = self.host.run([APFS_SYSTEMSNAPSHOT, '-s', name, '-v', self.mount_point], check=False)
        if r.returncode:
            self.emit({'ev': 'warning', 'text': 'no named snapshot before writing (%s); revert will use the booted '
                       'snapshot or a new one of the restored volume' % _text(r).strip().splitlines()[0]})
            return None
        s = next((s for s in self._snapshots() if s['name'] == name), None)
        if s is None:
            raise LP.PatchError('apfs_systemsnapshot -s %s succeeded but listSnapshots does not list it' % name)
        self.emit({'ev': 'snapshot', 'kind': 'pre', 'name': name})
        return {'name': name, 'uuid': s['uuid']}

    def _bless(self):
        """bless --create-snapshot; returns the new snapshot (None when it cannot be told apart)."""
        before = {s['name'] for s in self._snapshots()}
        r = self.host.run(BLESS_FORMS[self.bless](self.mount_point), check=False)
        new = [s for s in self._snapshots() if s['name'] not in before]
        if r.returncode:
            if new:        # it made a snapshot (and may have tagged it) before failing: boot the old one again
                self.host.run([APFS_SYSTEMSNAPSHOT, '-r', self.facts['booted']['name'], '-v', self.mount_point],
                              check=False)
            raise LP.PatchError('bless --create-snapshot failed (%d): %s' % (r.returncode, _text(r)))
        if len(new) != 1:
            self.emit({'ev': 'warning', 'text': 'bless made %d new snapshots: %s' % (len(new), [s['name'] for s in new])})
            return None
        if new[0]['root_to'] is False:
            self.emit({'ev': 'warning', 'text': 'listSnapshots does not show %s as RootTo' % new[0]['name']})
        return new[0]

    def snapshot(self, root, patch_id):
        s = self._bless()
        self.after_info = s
        self.emit({'ev': 'snapshot', 'kind': 'boot', 'name': s['name'] if s else None})
        return s['name'] if s else None

    def after_apply(self, record):
        try:                                 # best effort: never in the way of recording what was done
            restart = restart_report(self.host, self, record['files'])
        except Exception as x:
            self.emit({'ev': 'warning', 'text': 'could not list what must restart: %s' % x})
            restart = None
        for item in restart or ():
            self.emit(dict(item, ev='restart'))
        return {'snapshot_after': self.after_info, 'reboot': True, 'restart': restart}

    # ---- revert
    def choose_revert(self, applied, patch_id):
        live = [r for r in applied if r.get('backend') == self.name]
        if not live:
            raise LP.PatchError('no live patch to revert')
        latest = live[-1]
        if patch_id and patch_id != latest['id']:
            raise LP.PatchError('only the newest live patch (%s) can be reverted now' % latest['id'])
        booted = self.facts['booted']['name']
        if booted not in (latest.get('snapshot'), (latest.get('snapshot_booted') or {}).get('name')):
            raise LP.PatchError('the running system (%s) is neither the snapshot %s made nor the one it replaced'
                                % (booted, latest['id']))
        return latest

    def revert(self, root, record):
        names = {s['name'] for s in self._snapshots()}
        for kind in ('snapshot_booted', 'snapshot_pre'):
            name = (record.get(kind) or {}).get('name')
            if name and name in names:
                if not self._root_to(name):
                    break          # refused: boot a new snapshot of the restored volume instead
                self.reverted_to = {'name': name, 'kind': kind}
                self.emit({'ev': 'snapshot', 'kind': 'root-to', 'name': name})
                return name
        s = self._bless()          # neither survived: boot a new snapshot of the restored volume
        name = s['name'] if s else None
        self.reverted_to = {'name': name, 'kind': 'new'}
        self.emit({'ev': 'warning', 'text': 'the snapshot booted before %s could not be made the boot snapshot (gone, '
                   'or root-to refused): booting a new snapshot (%s) of the restored volume' % (record['id'], name)})
        return name

    def after_revert(self, record):
        return {'reboot': True, 'revert_boot': self.boot, 'reverted_to': self.reverted_to}


def restart_report(host, backend, files):
    """Best effort, read-only: running processes that map a written file (lsof's txt entries) and launchd jobs whose
    program is one. 'when' is 'reboot' for a System file (the running system keeps the booted snapshot's copy until
    then) and 'now' for a Data volume file (restart the process to load it)."""
    paths = {}
    for f in files:
        vol, sub = backend.volume(f['path'])
        when = 'now' if vol == 'data' else 'reboot'
        paths['/' + f['path']] = (f['path'], when)
        if vol == 'data':
            paths[backend.data_mount + '/' + sub] = (f['path'], when)
    if not paths:
        return []
    out, seen = [], set()
    r = host.run([LSOF, '-n', '-P', '-w', '-d', 'txt', '-F', 'pcn'], check=False, timeout=180)
    pid = command = None
    for line in r.stdout.decode(errors='replace').splitlines():
        tag, value = line[:1], line[1:]
        if tag == 'p':
            pid, command = int(value), None
        elif tag == 'c':
            command = value
        elif tag == 'n' and value in paths and (pid, value) not in seen:
            seen.add((pid, value))
            out.append({'kind': 'process', 'pid': pid, 'command': command, 'path': paths[value][0],
                        'when': paths[value][1]})
    for folder, domain in LAUNCHD:
        d = host.fs('/' + folder)
        for plist in sorted(d.glob('*.plist')) if d.is_dir() else ():
            try:
                job = plistlib.loads(plist.read_bytes())
            except Exception:
                continue
            program = job.get('Program') or (job.get('ProgramArguments') or [None])[0]
            if program in paths:
                out.append({'kind': 'launchd', 'label': job.get('Label'), 'domain': domain,
                            'path': paths[program][0], 'when': paths[program][1]})
    return out


def probe(host, state=STATE, mount_point=MOUNT_POINT):
    """Read-only: what a live patch would find and which of its steps would work here."""
    out = {'machine': host.machine(), 'euid': host.euid(), 'csr': host.csr_config(), 'boot': boot_time(host)}
    try:
        facts = discover(host)
    except LP.PatchError as x:
        facts = {'problems': [str(x)], 'snapshots': [], 'system': None, 'data': None, 'booted': {}, 'sealed': None,
                 'system_mounts': [], 'mount_table': []}
    out['facts'] = {k: v for k, v in facts.items() if k != 'mount_table'}
    r = host.run([CSRUTIL, 'authenticated-root', 'status'], check=False, timeout=15)
    text = r.stdout.decode(errors='replace')
    out['authenticated_root'] = ('disabled' if 'status: disabled' in text else 'enabled' if 'status: enabled' in text
                                 else 'unknown')
    problems = write_problems(host, facts, mount_point)
    recs = [r for r in LP.records(host.fs(state)) if r.get('backend') == MacOSLiveBackend.name]
    out['records'] = [{'id': r['id'], 'state': r['state'], 'snapshot': r.get('snapshot'),
                       'booted_before': (r.get('snapshot_booted') or {}).get('name')} for r in recs]
    csr = out['csr'] or 0
    names = {s['name'] for s in facts['snapshots']}
    tool = host.fs(APFS_SYSTEMSNAPSHOT).exists()
    pending = [r['id'] for r in recs if (r['state'] == 'applied' and r.get('boot') == out['boot'])
               or (r['state'] == 'reverted' and r.get('revert_boot') == out['boot'])]
    applied = [r for r in recs if r['state'] == 'applied']
    sealed = facts.get('sealed') == 'Yes' or any((n or '').startswith('com.apple.os.update-') for n in names)

    def step(name, ok, why):
        return {'step': name, 'ok': bool(ok), 'why': why}
    out['steps'] = [
        step('find the System and Data volumes', facts.get('system') and facts.get('data'),
             '; '.join(facts['problems']) or 'System %s, Data %s' % (facts['system']['device'], facts['data']['device'])),
        step('run as root on Intel', host.euid() == 0 and host.machine() == 'x86_64',
             'euid %d, %s' % (host.euid(), host.machine())),
        step('SIP allows writing and booting the System volume', csr & 0x802 == 0x802,
             'csr 0x%x (needs 0x802); csrutil says authenticated root %s' % (csr, out['authenticated_root'])),
        step('mount the System volume read-write at %s' % mount_point, not problems,
             '; '.join(problems) or 'nothing else has it mounted'),
        step('name a snapshot first (apfs_systemsnapshot -s)', tool and csr & 0x800, APFS_SYSTEMSNAPSHOT +
             (' present' if tool else ' missing')),
        step('bless --create-snapshot', host.fs(BLESS).exists() and csr & 0x800, 'as the image build does'),
        step('revert: root-to the booted snapshot (apfs_systemsnapshot -r)',
             tool and csr & 0x800 and facts['booted'].get('name') in names,
             'booted %s %s' % (facts['booted'].get('name'), 'is listed' if facts['booted'].get('name') in names
                               else 'is not listed')),
        step('revert: bless --last-sealed-snapshot', sealed,
             'a sealed snapshot exists' if sealed else 'no sealed snapshot (an image hao built has none): not usable'),
        step('no hao patch or revert since this boot', not pending, ', '.join(pending) or 'none'),
        step('the running system is the last live patch\'s snapshot', not applied or not applied[-1].get('snapshot')
             or applied[-1]['snapshot'] == facts['booted'].get('name'),
             'last applied %s' % (applied[-1]['id'] + ' -> ' + str(applied[-1].get('snapshot')) if applied else 'none')),
        step('lock free', not host.fs(state + '/lock').exists(), state + '/lock'),
    ]
    out['ok'] = all(s['ok'] for s in out['steps'] if not s['step'].startswith('revert: bless'))
    return out
