"""hao patch's live macOS backend against a recording fake Mac: discovery from diskutil plists, refusals, the exact
command sequences of apply and revert, rollback, idempotence, the probe. Nothing here runs a system command.
python3.14 -I -B -m unittest discover -s tools/hao/tests

fixtures/live: laptop-shaped `diskutil info -plist`, `diskutil apfs list -plist` and `listSnapshots -plist` output (an
Intel Mac booted from a bless snapshot of an unsealed System volume, with a second macOS install in the same
container), made from the key sets of a real macOS 27 capture with every identifier replaced (UUIDs 5A1E....)."""
import datetime
import hashlib
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'src'))
import livepatch as LP   # noqa: E402
import macos as MAC      # noqa: E402

FIX = HERE / 'fixtures' / 'live'
T0 = datetime.datetime(2026, 11, 12, 18, 0, 0)
MNT = MAC.MOUNT_POINT
SNAP = MAC.APFS_SYSTEMSNAPSHOT
BOOTED = 'com.apple.bless.5A1E0051-0000-4000-8000-000000000051'
ASCII = 'usr/share/misc/ascii'
PROBE = 'usr/share/hao/9a-probe.txt'
DATA = 'Library/Application Support/hao-9a/data-probe.txt'
DAEMON = 'usr/libexec/hao-9a-daemon'
KEXT = 'System/Library/Extensions/Hao9a.kext/Contents/MacOS/Hao9a'
METAL = 'System/Library/Frameworks/Metal.framework/Versions/A/Metal'
OWNER = '%d:%d' % (os.getuid(), os.getgid())     # what the fixture files really have: a rerun changes nothing
FIRMLINKS = '/Applications\tApplications\n/Library\tLibrary\n/System/Library/Caches\tSystem/Library/Caches\n' \
            '/Users\tUsers\n/private\tprivate\n/usr/local\tusr/local\n'


def sha(data):
    return hashlib.sha256(data).hexdigest()


class FakeHost(MAC.Host):
    """A Mac in three folders: booted/ (the snapshot mounted at /), live/ (the System volume itself, seen at MNT
    once mounted) and data/ (the Data volume at /System/Volumes/Data). Commands are answered from fixtures/live and
    recorded; mount, apfs_systemsnapshot and bless change the fake's state as they would the Mac's."""

    def __init__(self, base):
        self.base = Path(base)
        self.booted, self.live, self.data = self.base / 'booted', self.base / 'live', self.base / 'data'
        self.calls = []
        self.csr, self.uid, self.arch = 0x803, 0, 'x86_64'
        self.root_info = plistlib.loads((FIX / 'info-root.plist').read_bytes())
        self.snaps = {d: plistlib.loads((FIX / ('snapshots-%s.plist' % d)).read_bytes())['Snapshots']
                      for d in ('disk1s5', 'disk1s7')}
        self.table = (FIX / 'mount.txt').read_text().splitlines()
        self.boot = 1793900000
        self.fail = lambda argv: None           # returns (returncode, stderr) to make a command fail
        self.lsof = b''
        self.counter = 0x60

    # Host
    def csr_config(self):
        return self.csr

    def euid(self):
        return self.uid

    def machine(self):
        return self.arch

    def fs(self, path):
        path = str(path)
        for prefix, local in ((str(self.base), self.base), (MNT, self.live), ('/System/Volumes/Data', self.data),
                              ('/', self.booted)):
            if path == prefix or path.startswith(prefix.rstrip('/') + '/'):
                return local / path[len(prefix):].lstrip('/')
        raise AssertionError(path)

    def run(self, argv, check=True, timeout=120):
        argv = [str(a) for a in argv]
        self.calls.append(argv)
        failed = self.fail(argv)
        if failed:
            r = subprocess.CompletedProcess(argv, failed[0], b'', failed[1].encode())
        else:
            r = subprocess.CompletedProcess(argv, 0, self.answer(argv), b'')
        if check and r.returncode:
            raise LP.PatchError('%s failed (%d): %s' % (' '.join(argv), r.returncode, failed[1]))
        return r

    def answer(self, argv):
        cmd = argv[0]
        if argv[:3] == [MAC.DISKUTIL, 'info', '-plist']:
            if argv[3] == '/':
                return plistlib.dumps(self.root_info)
            return (FIX / ('info-%s.plist' % argv[3])).read_bytes()
        if argv[:4] == [MAC.DISKUTIL, 'apfs', 'list', '-plist']:
            return (FIX / ('apfs-list-%s.plist' % argv[4])).read_bytes()
        if argv[:4] == [MAC.DISKUTIL, 'apfs', 'listSnapshots', '-plist']:
            return plistlib.dumps({'Snapshots': self.snaps.get(argv[4], [])})
        if cmd == MAC.MOUNT and len(argv) == 1:
            return ('\n'.join(self.table) + '\n').encode()
        if cmd == MAC.MOUNT:
            assert argv[1:6] == ['-o', 'nobrowse', '-t', 'apfs', '/dev/disk1s5'], argv
            assert not any(' on %s ' % argv[6] in l for l in self.table), 'already mounted'
            self.table.append('/dev/disk1s5 on %s (apfs, local, journaled, nobrowse)' % argv[6])
            return b''
        if cmd == MAC.UMOUNT:
            self.table = [l for l in self.table if ' on %s (' % argv[1] not in l]
            return b''
        if cmd == SNAP:
            assert self.mounted(argv[4]), 'apfs_systemsnapshot on an unmounted volume'
            if argv[1] == '-s':
                self.add_snapshot(argv[2])
            else:
                assert any(s['SnapshotName'] == argv[2] for s in self.snaps['disk1s5']), argv
                for s in self.snaps['disk1s5']:
                    s['RootTo'] = s['SnapshotName'] == argv[2]
            return b''
        if cmd == MAC.BLESS:
            assert '--create-snapshot' in argv and self.mounted(argv[2].replace('/System/Library/CoreServices', ''))
            self.counter += 1
            name = 'com.apple.bless.5A1E%04X-0000-4000-8000-%012X' % (self.counter, self.counter)
            self.add_snapshot(name)
            for s in self.snaps['disk1s5']:
                s['RootTo'] = s['SnapshotName'] == name
            return b''
        if cmd == MAC.LSOF:
            return self.lsof
        if cmd == MAC.SYSCTL:
            return ('{ sec = %d, usec = 1 } Thu Nov 12 18:00:00 2026\n' % self.boot).encode()
        if cmd == MAC.CSRUTIL:
            return b'Authenticated Root status: disabled\n'
        raise AssertionError('unexpected command %s' % argv)

    # state
    def mounted(self, point):
        return any(l.startswith('/dev/disk1s5 on %s (' % point) for l in self.table)

    def add_snapshot(self, name):
        self.counter += 1
        uuid = '5A1E%04X-0000-4000-8000-%012X' % (self.counter, self.counter)
        self.snaps['disk1s5'].append({'LimitingContainerShrink': False, 'Purgeable': False, 'RevertTo': False,
                                      'RootTo': False, 'SnapshotName': name, 'SnapshotUUID': uuid,
                                      'SnapshotXID': 1200 + self.counter})
        self.contents = getattr(self, 'contents', {})
        self.contents[name] = self.tree(self.live)

    @staticmethod
    def tree(folder):
        return {str(p.relative_to(folder)): p.read_bytes() for p in sorted(folder.rglob('*')) if p.is_file()}

    def reboot(self):
        """Boot the snapshot tagged RootTo (else the one booted): / becomes its content."""
        snaps = self.snaps['disk1s5']
        s = next((s for s in snaps if s['RootTo']), None) or next(s for s in snaps
                                                                  if s['SnapshotUUID'] == self.root_info['APFSSnapshotUUID'])
        content = self.contents.get(s['SnapshotName']) if hasattr(self, 'contents') else None
        if content is not None:
            shutil.rmtree(self.booted)
            for rel, data in content.items():
                (self.booted / rel).parent.mkdir(parents=True, exist_ok=True)
                (self.booted / rel).write_bytes(data)
        for s2 in snaps:
            s2['RootTo'] = False
        self.root_info.update(APFSSnapshotName=s['SnapshotName'], APFSSnapshotUUID=s['SnapshotUUID'])
        self.table = [l for l in self.table if not l.startswith('/dev/disk1s5 on ')]
        self.boot += 1000


class Live(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.host = FakeHost(self.d)
        self.state = self.d / 'state'
        system = {ASCII: b'ascii table\n', METAL: b'cached metal',
                  'System/Library/CoreServices/SystemVersion.plist': plistlib.dumps({'ProductBuildVersion': '26B131'}),
                  'usr/share/firmlinks': FIRMLINKS.encode(),
                  SNAP.lstrip('/'): b'tool', MAC.BLESS.lstrip('/'): b'tool',
                  'System/Volumes/Preboot/Cryptexes/OS/System/Library/dyld/dyld_shared_cache_x86_64h.map':
                      ('mapping  EX  720KB 0x7FF800000000\n/usr/lib/libSystem.B.dylib\n/%s\n' % METAL).encode(),
                  'System/Library/LaunchDaemons/org.hao.9a.plist':
                      plistlib.dumps({'Label': 'org.hao.9a', 'ProgramArguments': ['/' + DAEMON, '-x']})}
        for folder in (self.host.booted, self.host.live):
            for rel, data in system.items():
                (folder / rel).parent.mkdir(parents=True, exist_ok=True)
                (folder / rel).write_bytes(data)
        (self.host.data / 'Library').mkdir(parents=True)
        self.host.contents = {BOOTED: self.host.tree(self.host.live)}
        self.host.lsof = ('p88\ncloginwindow\nftxt\nn/usr/lib/dyld\np412\nchao-9a-daemon\nftxt\nn/%s\n' % DAEMON).encode()
        self.calls_before = 0
        self.attrs_calls = []

    def attributes(self):
        calls = self.attrs_calls
        real = lambda p, bits: os.chflags(p, bits & 0xffff, follow_symlinks=False)
        return LP.Attributes(self.host.booted, privileged=True, chown=lambda p, u, g: calls.append(('chown', p, u, g)),
                             chflags=lambda p, bits: (calls.append(('chflags', p, bits)), real(p, bits)))

    def overlay(self, name='hao-9a', phase='live-capable', files=None):
        o = self.d / ('overlay-' + name)
        files = files or {PROBE: b'hao 9a probe\n', ASCII: b'ascii table\n# hao 9a\n', DATA: b'data probe\n',
                          DAEMON: b'\xcf\xfa\xed\xfedaemon', KEXT: b'kext', METAL: b'patched metal'}
        m = {'format': 1, 'pack': name, 'phase': phase, 'files': {}}
        for path, data in files.items():
            (o / 'files' / path).parent.mkdir(parents=True, exist_ok=True)
            (o / 'files' / path).write_bytes(data)
            m['files'][path] = {'sha256': sha(data), 'mode': '0644', 'owner': OWNER, 'flags': []}
        (o / 'manifest.json').write_text(json.dumps(m))
        return LP.load_overlay(o)

    def backend(self, **kw):
        return MAC.MacOSLiveBackend(self.host, acknowledged=True, **kw)

    def apply(self, overlays=None, **kw):
        events = []
        rec = LP.apply(overlays or [self.overlay()], Path('/'), self.state, backend=self.backend(), now=T0,
                       on_event=events.append, attributes=self.attributes(), **kw)
        return rec, events

    def revert(self, **kw):
        events = []
        rec = LP.revert(Path('/'), self.state, backend=self.backend(), on_event=events.append,
                        attributes=self.attributes(), **kw)
        return rec, events

    def new_calls(self):
        out = self.host.calls[self.calls_before:]
        self.calls_before = len(self.host.calls)
        return out

    def mutating(self, calls):
        return [c for c in calls if c[0] in (MAC.UMOUNT, MAC.BLESS, SNAP) or (c[0] == MAC.MOUNT and len(c) > 1)]


class Discovery(Live):
    def test_volumes_from_plists(self):
        f = MAC.discover(self.host)
        self.assertEqual(f['problems'], [])
        self.assertEqual((f['system']['device'], f['data']['device'], f['data']['mount'], f['container']),
                         ('disk1s5', 'disk1s1', '/System/Volumes/Data', 'disk1'))
        self.assertEqual(f['booted'], {'name': BOOTED, 'uuid': '5A1E0051-0000-4000-8000-000000000051'})
        self.assertEqual([(s['name'], s['booted']) for s in f['snapshots']], [(BOOTED, True)])
        self.assertEqual(f['system_mounts'], [])
        # the other install's System (disk1s7) and Data (disk1s8) are in another volume group: never listed
        self.assertNotIn([MAC.DISKUTIL, 'apfs', 'listSnapshots', '-plist', 'disk1s7'], self.host.calls)

    def test_the_snapshot_device_name_is_not_used(self):
        self.host.root_info.update(DeviceIdentifier='disk1s10s12', DeviceNode='/dev/disk1s10s12')
        f = MAC.discover(self.host)
        self.assertEqual(f['system']['device'], 'disk1s5')        # stripping "s12" would give disk1s10

    def test_problems(self):
        self.host.root_info['APFSSnapshot'] = False
        self.assertIn('not mounted from a snapshot', MAC.discover(self.host)['problems'][0])
        self.host.root_info['APFSSnapshot'] = True
        self.host.root_info['APFSSnapshotUUID'] = '5A1E0FFF-0000-4000-8000-000000000FFF'   # in no System volume
        self.assertIn('0 System volumes of disk1 hold the booted snapshot', MAC.discover(self.host)['problems'][0])


class Refusals(Live):
    def refused(self, pattern, pristine=True, **backend):
        events = []
        with self.assertRaisesRegex(LP.PatchError, pattern):
            LP.apply([self.overlay()], Path('/'), self.state, backend=MAC.MacOSLiveBackend(self.host, **backend),
                     now=T0, on_event=events.append, attributes=self.attributes())
        self.assertEqual(self.mutating(self.host.calls), [])
        if pristine:
            self.assertEqual((self.host.live / ASCII).read_bytes(), b'ascii table\n')
            self.assertFalse((self.host.live / PROBE).exists())
        return events

    def test_not_proven_without_the_flag(self):
        self.refused('not proven yet.*--i-have-a-backup')
        self.assertEqual(self.host.calls, [])                       # refused before asking the Mac anything

    def test_not_root(self):
        self.host.uid = 501
        self.refused('not root', acknowledged=True)

    def test_not_intel(self):
        self.host.arch = 'arm64'
        self.refused('not an Intel Mac', acknowledged=True)

    def test_authenticated_root_on(self):
        self.host.csr = 0x0
        self.refused('authenticated root is on.*\n.*filesystem protection is on', acknowledged=True)
        self.host.csr = 0x800
        self.refused('filesystem protection is on', acknowledged=True)

    def test_system_volume_mounted_by_someone_else(self):
        self.host.table.append('/dev/disk1s5 on /System/Volumes/Update/mnt1 (apfs, local, journaled, nobrowse)')
        self.refused('mounted at /System/Volumes/Update/mnt1 by something else', acknowledged=True)

    def test_patch_waiting_for_a_reboot(self):
        self.apply()
        self.host.calls.clear()
        self.refused('applied in this boot and is not running yet: reboot first', False, acknowledged=True)

    def test_running_system_is_not_the_last_patch(self):
        self.apply()
        self.host.reboot()
        for s in self.host.snaps['disk1s5']:            # someone else re-tagged the old snapshot and rebooted
            s['RootTo'] = s['SnapshotName'] == BOOTED
        self.host.reboot()
        self.host.calls.clear()
        self.refused('is not the snapshot hao-20261112-180000 made', False, acknowledged=True)


class ApplyRevert(Live):
    def test_apply_command_sequence_files_and_record(self):
        rec, events = self.apply()
        pre = 'hao-pre-hao-20261112-180000'
        new = next(s['SnapshotName'] for s in self.host.snaps['disk1s5'] if s['RootTo'])
        self.assertEqual(self.host.calls, [
            [MAC.DISKUTIL, 'info', '-plist', '/'],
            [MAC.DISKUTIL, 'apfs', 'list', '-plist', 'disk1'],
            [MAC.DISKUTIL, 'info', '-plist', 'disk1s1'],
            [MAC.DISKUTIL, 'info', '-plist', 'disk1s5'],
            [MAC.DISKUTIL, 'info', '-plist', 'disk1s7'],
            [MAC.DISKUTIL, 'info', '-plist', 'disk1s8'],
            [MAC.DISKUTIL, 'apfs', 'listSnapshots', '-plist', 'disk1s5'],
            [MAC.MOUNT],
            [MAC.SYSCTL, '-n', 'kern.boottime'],
            [MAC.MOUNT, '-o', 'nobrowse', '-t', 'apfs', '/dev/disk1s5', MNT],
            [MAC.MOUNT],
            [SNAP, '-s', pre, '-v', MNT],
            [MAC.DISKUTIL, 'apfs', 'listSnapshots', '-plist', 'disk1s5'],
            [MAC.DISKUTIL, 'apfs', 'listSnapshots', '-plist', 'disk1s5'],
            [MAC.BLESS, '--folder', MNT + '/System/Library/CoreServices', '--bootefi', '--create-snapshot'],
            [MAC.DISKUTIL, 'apfs', 'listSnapshots', '-plist', 'disk1s5'],
            [MAC.UMOUNT, MNT],
            [MAC.LSOF, '-n', '-P', '-w', '-d', 'txt', '-F', 'pcn'],
        ])
        # System files went to the volume, not to the running snapshot; the Data file to the Data volume
        self.assertEqual((self.host.live / PROBE).read_bytes(), b'hao 9a probe\n')
        self.assertEqual((self.host.live / ASCII).read_bytes(), b'ascii table\n# hao 9a\n')
        self.assertEqual((self.host.booted / ASCII).read_bytes(), b'ascii table\n')
        self.assertEqual((self.host.data / DATA).read_bytes(), b'data probe\n')
        self.assertFalse((self.host.live / 'Library').exists())
        # the kext and the dyld cache's Metal are not written live
        self.assertFalse((self.host.live / KEXT).exists())
        self.assertEqual((self.host.live / METAL).read_bytes(), b'cached metal')
        self.assertEqual({f['path']: f['reason'].split(' (')[0] for f in rec['needs_recovery_files']},
                         {KEXT: 'a kernel collection member', METAL: 'in the dyld shared cache'})
        self.assertEqual((rec['snapshot_booted']['name'], rec['snapshot_pre']['name'], rec['snapshot']),
                         (BOOTED, pre, new))
        self.assertEqual((rec['state'], rec['system_device'], rec['reboot'], rec['boot']),
                         ('applied', 'disk1s5', True, 1793900000))
        self.assertEqual(sorted((r['kind'], r.get('label') or r.get('command'), r['when']) for r in rec['restart']),
                         [('launchd', 'org.hao.9a', 'reboot'), ('process', 'hao-9a-daemon', 'reboot')])
        self.assertEqual([e['ev'] for e in events],
                         ['live', 'plan', 'mount', 'snapshot'] + ['file.write'] * 4 + ['snapshot', 'restart', 'restart',
                                                                                          'done'])
        self.assertTrue(events[-1]['reboot'])
        self.assertEqual(self.host.contents[new][ASCII], b'ascii table\n# hao 9a\n')   # what the next boot runs

    def test_reboot_revert_reboot(self):
        self.apply()
        self.host.reboot()
        self.assertEqual((self.host.booted / PROBE).read_bytes(), b'hao 9a probe\n')     # the patch is running
        self.new_calls()
        rec, events = self.revert()
        self.assertEqual(self.mutating(self.new_calls()), [
            [MAC.MOUNT, '-o', 'nobrowse', '-t', 'apfs', '/dev/disk1s5', MNT],
            [SNAP, '-r', BOOTED, '-v', MNT],
            [MAC.UMOUNT, MNT]])
        self.assertEqual((rec['state'], rec['snapshot_reverted_to'], rec['reverted_to']['kind']),
                         ('reverted', BOOTED, 'snapshot_booted'))
        self.assertEqual((self.host.live / ASCII).read_bytes(), b'ascii table\n')       # the volume as booted before
        self.assertFalse((self.host.live / PROBE).exists())
        self.assertFalse((self.host.live / 'usr/share/hao').exists())
        self.assertFalse((self.host.data / DATA).exists())                              # Data files undone at once
        self.assertEqual(events[-1]['ev'], 'reverted')
        self.host.reboot()
        self.assertEqual(self.host.root_info['APFSSnapshotName'], BOOTED)
        self.assertEqual((self.host.booted / ASCII).read_bytes(), b'ascii table\n')
        self.assertFalse((self.host.booted / PROBE).exists())

    def test_revert_before_the_reboot(self):
        self.apply()
        self.new_calls()
        rec, _ = self.revert()
        self.assertIn([SNAP, '-r', BOOTED, '-v', MNT], self.new_calls())
        self.assertFalse((self.host.live / PROBE).exists())

    def test_revert_falls_back_to_the_named_snapshot_then_to_a_new_one(self):
        self.apply()
        self.host.reboot()
        self.host.snaps['disk1s5'] = [s for s in self.host.snaps['disk1s5'] if s['SnapshotName'] != BOOTED]
        self.new_calls()
        rec, _ = self.revert()
        self.assertIn([SNAP, '-r', 'hao-pre-hao-20261112-180000', '-v', MNT], self.new_calls())
        self.assertEqual(rec['reverted_to']['kind'], 'snapshot_pre')

    def test_revert_with_no_old_snapshot_left_blesses_the_restored_volume(self):
        self.apply()
        self.host.reboot()
        self.host.snaps['disk1s5'] = [s for s in self.host.snaps['disk1s5'] if s['SnapshotName'].startswith(
            'com.apple.bless.') and s['SnapshotName'] != BOOTED]
        self.new_calls()
        rec, events = self.revert()
        self.assertIn([MAC.BLESS, '--folder', MNT + '/System/Library/CoreServices', '--bootefi', '--create-snapshot'],
                      self.new_calls())
        self.assertEqual(rec['reverted_to']['kind'], 'new')
        self.assertIn('could not be made the boot snapshot', [e for e in events if e['ev'] == 'warning'][0]['text'])
        self.host.reboot()
        self.assertEqual((self.host.booted / ASCII).read_bytes(), b'ascii table\n')

    def test_cache_images_are_written_when_dyld_loads_roots(self):
        """With CSR_ALLOW_APPLE_INTERNAL reported (ic_csr_internal=1 on hao's images) dyld loads a file at a cache
        image's path instead of the cache's copy, so the cache's Metal is written like any file; kexts still wait."""
        self.host.csr = 0x813
        rec, events = self.apply()
        self.assertEqual((self.host.live / METAL).read_bytes(), b'patched metal')
        self.assertEqual((self.host.booted / METAL).read_bytes(), b'cached metal')
        self.assertFalse((self.host.live / KEXT).exists())
        self.assertEqual([f['path'] for f in rec['needs_recovery_files']], [KEXT])
        self.assertIn(METAL, [e['path'] for e in events if e['ev'] == 'file.write'])

    def test_cache_images_are_written_when_hao_dyld_is_edited(self):
        """Without the CSR bit (no ic_csr_internal boot-arg), an installed dyld whose internalInstall() answers true
        (hao-runtime's replace/dyld) loads roots too: the cache's Metal is written live; an unedited dyld waits."""
        self.host.csr = 0x803
        dyld = self.host.booted / 'usr/lib/dyld'
        dyld.parent.mkdir(parents=True, exist_ok=True)
        dyld.write_bytes(b'\0' * 64 + LP.DYLD_INTERNAL_EDITED + b'\0' * 64)
        rec, events = self.apply()
        self.assertEqual((self.host.live / METAL).read_bytes(), b'patched metal')
        self.assertEqual([f['path'] for f in rec['needs_recovery_files']], [KEXT])

    def test_unedited_dyld_without_csr_bit_keeps_cache_images_for_recovery(self):
        self.host.csr = 0x803
        dyld = self.host.booted / 'usr/lib/dyld'
        dyld.parent.mkdir(parents=True, exist_ok=True)
        dyld.write_bytes(bytes.fromhex('554889e5bf10000000e84475feff85c00f94c05dc3'))
        rec, events = self.apply()
        self.assertIn(METAL, [f['path'] for f in rec['needs_recovery_files']])

    def test_second_apply_writes_nothing(self):
        self.apply()
        self.host.reboot()
        self.new_calls()
        rec, events = self.apply()
        self.assertIsNone(rec)
        self.assertEqual(self.mutating(self.new_calls()), [])                   # no mount, no snapshot, no bless
        self.assertEqual(events[-1], dict(events[-1], ev='done', id=None, written=0))
        self.assertEqual(len(LP.records(self.state)), 1)

    def test_oclp_bless_form(self):
        LP.apply([self.overlay()], Path('/'), self.state, backend=self.backend(bless='oclp'), now=T0,
                 attributes=self.attributes())
        self.assertIn([MAC.BLESS, '--folder', MNT + '/System/Library/CoreServices', '--bootefi', '--create-snapshot'],
                      self.host.calls)


class Rollback(Live):
    def test_bless_failure_puts_every_file_back(self):
        self.host.fail = lambda argv: (1, 'bless: Could not create snapshot') if argv[0] == MAC.BLESS else None
        events = []
        with self.assertRaisesRegex(LP.PatchError, 'bless --create-snapshot failed'):
            LP.apply([self.overlay()], Path('/'), self.state, backend=self.backend(), now=T0, on_event=events.append,
                     attributes=self.attributes())
        self.assertEqual((self.host.live / ASCII).read_bytes(), b'ascii table\n')
        self.assertFalse((self.host.live / PROBE).exists())
        self.assertFalse((self.host.data / DATA).exists())
        self.assertEqual(self.host.calls[-1], [MAC.UMOUNT, MNT])
        rec = LP.records(self.state)[0]
        self.assertEqual(rec['state'], 'rolled-back')
        self.assertIn('rolled-back', [e['ev'] for e in events])
        self.assertFalse(any(s['RootTo'] for s in self.host.snaps['disk1s5']))     # the boot snapshot is unchanged

    def test_failure_in_the_middle_of_writing(self):
        real, n = LP._install, []

        def install(*args):
            n.append(1)
            if len(n) == 3:
                raise OSError(28, 'No space left on device')
            return real(*args)
        LP._install = install
        try:
            with self.assertRaisesRegex(OSError, 'No space left'):
                self.apply()
        finally:
            LP._install = real
        self.assertEqual((self.host.live / ASCII).read_bytes(), b'ascii table\n')
        self.assertFalse((self.host.live / PROBE).exists())
        self.assertFalse((self.host.data / DATA).exists())
        self.assertFalse(any(c[0] == MAC.BLESS for c in self.host.calls))
        self.assertEqual(LP.records(self.state)[0]['state'], 'rolled-back')

    def test_a_refused_root_to_boots_a_new_snapshot_of_the_restored_volume(self):
        # laptop 2026-11-12: apfs_systemsnapshot needs an entitlement root lacks (-s was refused there)
        self.apply()
        self.host.reboot()
        self.host.fail = lambda argv: (1, 'fs_snapshot_root failed') if argv[:2] == [SNAP, '-r'] else None
        rec, _ = self.revert()
        self.assertEqual(rec['state'], 'reverted')
        self.assertNotEqual(rec['snapshot_reverted_to'], BOOTED)
        self.assertTrue(any(c[0] == MAC.BLESS for c in self.host.calls))
        self.host.reboot()
        self.assertEqual((self.host.booted / ASCII).read_bytes(), b'ascii table\n')

    def test_a_refused_pre_snapshot_still_patches(self):
        self.host.fail = lambda argv: (255, 'fs_snapshot_create: Operation not permitted') if argv[:2] == [SNAP, '-s'] else None
        rec, events = self.apply()
        self.assertEqual(rec['state'], 'applied')
        self.assertIsNone(rec.get('snapshot_pre'))
        self.assertTrue(any(e.get('ev') == 'warning' and 'no named snapshot' in e.get('text', '') for e in events))

    def test_no_lsof_still_records_the_patch(self):
        self.host.fail = lambda argv: (1, 'lsof: not found') if argv[0] == MAC.LSOF else None
        rec, events = self.apply()
        self.assertEqual(LP.records(self.state)[0]['state'], 'applied')
        self.assertEqual([r['kind'] for r in rec['restart']], ['launchd'])        # launchd jobs come from plists

    def test_volume_written_since_boot_is_refused_after_mounting(self):
        (self.host.live / ASCII).write_bytes(b'someone else\n')
        with self.assertRaisesRegex(LP.PatchError, 'differs between the running system and its System volume'):
            self.apply()
        self.assertEqual((self.host.live / ASCII).read_bytes(), b'someone else\n')
        self.assertFalse(any(c[0] in (SNAP, MAC.BLESS) for c in self.host.calls))
        self.assertEqual(self.host.calls[-1], [MAC.UMOUNT, MNT])


class Deferred(Live):
    def test_reported_in_order_after_the_writes(self):
        deferred = [{'path': 'System/Library/KernelCollections/BootKernelExtensions.kc', 'pack': 'base',
                     'recipe': 'darwin27-kernel', 'reason': 'kc.link', 'after': []},
                    {'path': 'usr/lib/hao/hao-9a.stamp', 'pack': 'hao-9a', 'recipe': 'x', 'reason': 'hao.stamp',
                     'after': []},
                    {'path': 'usr/lib/hao/native-identities', 'pack': 'base', 'recipe': 'golden-gate-native-identities',
                     'reason': 'generated on the target', 'after': ['overlays']}]
        rec, events = self.apply(deferred=deferred)
        d = [e for e in events if e['ev'] == 'deferred']
        self.assertEqual([(e['path'].split('/')[-1], e['status'], e['stale']) for e in d],
                         [('BootKernelExtensions.kc', 'needs-recovery', False), ('hao-9a.stamp', 'not-run', False),
                          ('native-identities', 'needs-recovery', True)])
        self.assertLess(max(i for i, e in enumerate(events) if e['ev'] == 'file.write'),
                        min(i for i, e in enumerate(events) if e['ev'] == 'deferred'))


class Probe(Live):
    def test_reads_only_and_says_what_would_work(self):
        r = MAC.probe(self.host, str(self.state))
        self.assertEqual(self.mutating(self.host.calls), [])
        steps = {s['step'].split(' (')[0]: s['ok'] for s in r['steps']}
        self.assertTrue(r['ok'], r['steps'])
        self.assertFalse(steps['revert: bless --last-sealed-snapshot'])
        self.assertTrue(steps['revert: root-to the booted snapshot'])
        self.assertEqual(r['authenticated_root'], 'disabled')
        self.host.csr, self.host.arch = 0, 'arm64'
        r = MAC.probe(self.host, str(self.state))
        self.assertFalse(r['ok'])
        self.assertFalse(steps['revert: bless --last-sealed-snapshot'])
        self.assertEqual({s['step'].split(' ')[0] for s in r['steps'] if not s['ok']},
                         {'run', 'SIP', 'mount', 'name', 'bless', 'revert:'})

    def test_command_line_refuses_live_without_the_flag(self):
        self.overlay()
        r = subprocess.run([sys.executable, '-I', str(HERE.parent / 'hao'), 'patch', str(self.d / 'overlay-hao-9a'),
                            '--live'], capture_output=True, text=True)
        self.assertEqual(r.returncode, 1)
        self.assertIn('not proven yet', r.stderr)


if __name__ == '__main__':
    unittest.main()
