"""stick.py with a fake diskutil: partitioning makes two folders as the stick's mounted volumes; no real disk."""
import io
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
TOOL = HERE.parent
sys.path.insert(0, str(TOOL / 'src'))
import stick as STICK      # noqa: E402

REPO = TOOL.parents[1]
NV = '7C436110-AB2A-4BBB-A880-FE41995C9F82'
IAS = '4D1EDE05-38C7-4A6A-9CC6-4BCCA8B38C14'


def oclp_config():
    return {'Misc': {'Security': {'DmgLoading': 'Signed', 'ScanPolicy': 0x10F0103}, 'Boot': {'LauncherOption': 'Full',
                                                                                            'HideAuxiliary': True},
                     'Tools': [{'Name': 'OpenShell.efi', 'Enabled': True, 'Auxiliary': True}]},
            'UEFI': {'Quirks': {'RequestBootVarRouting': True},
                     'Drivers': [{'Path': 'OpenCanopy.efi', 'Enabled': True}, {'Path': 'ResetNvramEntry.efi', 'Enabled': True}]},
            'Kernel': {'Add': [{'BundlePath': 'Lilu.kext', 'Enabled': True},
                               {'BundlePath': 'AutoPkgInstaller.kext', 'Enabled': True}], 'Patch': []},
            'NVRAM': {'Add': {NV: {'boot-args': 'keepsyms=1 -lilubetaall'},
                              '4D1FDA02-38C7-4A6A-9CC6-4BCCA8B30102': {'revpatch': 'sbvmm,asset'},
                              IAS: {'IASInstallPhaseList': b'x', 'DefaultBackgroundColor': b'\0\0\0\0'}},
                      'Delete': {IAS: ['IASInstallPhaseList', 'DefaultBackgroundColor']}}}


def esp_tar(path, config=None):
    with tarfile.open(path, 'w') as t:
        for name, data in (('EFI/OC/OpenCore.efi', b'MZ opencore'), ('EFI/OC/.contentFlavour', b'OCLP'),
                           ('EFI/OC/Kexts/Lilu.kext/Contents/Info.plist', b'<plist/>'),
                           ('EFI/OC/config.plist', plistlib.dumps(config or oclp_config())),
                           ('System/Library/CoreServices/boot.efi', b'boot')):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            t.addfile(info, io.BytesIO(data))


class FakeHost:
    """diskutil partitionDisk makes ROOT/HAOBOOT and ROOT/HAO; list/info describe them as mounted there."""

    def __init__(self, root):
        self.root, self.calls, self.partitioned = Path(root), [], False
        self.moved = {}                 # mount point -> another disk's partition (a swapped drive)
        self.uuid = 'UUID-1'            # the VolumeUUID diskutil reports for mounted partitions
        self.unmounted = set()          # partitions diskutil reports with no mount point until `diskutil mount`
        self.efi_needs_root = False     # a real drive: macOS mounts its EFI partition only for root

    def run(self, argv, check=True, timeout=120):
        argv = [str(a) for a in argv]
        self.calls.append(argv)
        out = b''
        if argv[:2] == ['sudo', '-n']:
            argv = argv[2:]                 # the erase as a user: the same command through sudo
        if argv[:2] == ['diskutil', 'partitionDisk']:
            for name in ('EFI', 'HAOBOOT', 'HAO'):
                (self.root / name).mkdir()
            self.partitioned = True
        elif argv[:3] == ['diskutil', 'list', '-plist']:
            parts = [{'DeviceIdentifier': 'disk4s1', 'VolumeName': 'EFI', 'Content': 'EFI'}]
            if self.partitioned:
                parts += [{'DeviceIdentifier': 'disk4s2', 'VolumeName': 'HAOBOOT'},
                          {'DeviceIdentifier': 'disk4s3', 'VolumeName': 'HAO'}]
            out = plistlib.dumps({'AllDisksAndPartitions': [{'DeviceIdentifier': 'disk4', 'Partitions': parts}]})
        elif argv[:2] == ['diskutil', 'mount']:
            if self.calls[-1][:2] != ['sudo', '-n'] and self.efi_needs_root and argv[2] == 'disk4s1':
                return subprocess.CompletedProcess(argv, 1, b'', b'Volume on disk4s1 failed to mount')
            self.unmounted.discard(argv[2])
        elif argv[:3] == ['diskutil', 'info', '-plist']:
            devs = {'disk4s1': 'EFI', 'disk4s2': 'HAOBOOT', 'disk4s3': 'HAO'}
            if argv[3] in self.unmounted:
                out = plistlib.dumps({'DeviceIdentifier': argv[3], 'ParentWholeDisk': 'disk4'})
            elif argv[3] in devs:
                out = plistlib.dumps(dict({'MountPoint': str(self.root / devs[argv[3]]), 'DeviceIdentifier': argv[3],
                                      'ParentWholeDisk': 'disk4'}, **({'VolumeUUID': self.uuid} if self.uuid else {})))
            else:                               # a mount point: which partition is mounted there now
                dev = self.moved.get(argv[3]) or {str(self.root / n): d for d, n in devs.items()}.get(argv[3])
                out = plistlib.dumps(dict({'DeviceIdentifier': dev, 'ParentWholeDisk': dev[:5] if dev else None},
                                          **({'VolumeUUID': self.uuid} if self.uuid else {})))
        elif argv[:2] == ['hdiutil', 'attach']:
            out = plistlib.dumps({'system-entities': [{'dev-entry': '/dev/disk7s1'}, {'dev-entry': '/dev/disk7'}]})
        return subprocess.CompletedProcess(argv, 0, out, b'')


class StickTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)
        self.bs = self.d / 'BaseSystem-x86_64.dmg'
        self.bs.write_bytes(b'koly' * 1000)
        self.esp = self.d / 'esp.tar'
        esp_tar(self.esp)
        kit = self.kit = self.d / 'kit'
        for rel, data in (('kit.json', b'{"schema": 1}'), ('py-x86_64/bin/python3.12', b'#!py'),
                          ('hooks/02-userspace/inputs/u/filesystem.dmg', b'U' * 2048),
                          ('hooks/15-identities/inputs/u/filesystem.dmg', b'U' * 2048),
                          ('hooks/15-identities/inputs/u/other.dmg', b'V' * 2048),
                          ('cryptex-cache/key.json', b'{}'), ('cryptex-cache/in/abc/os.dmg', b'C' * 4096)):
            (kit / rel).parent.mkdir(parents=True, exist_ok=True)
            (kit / rel).write_bytes(data)
        os.symlink('python3.12', kit / 'py-x86_64/bin/python3')
        top = self.top = self.d / 'hao-builder'
        for rel in ('tools/hao/hao', 'pipeline/lock.py', 'store/inputs/huge', 'tools/hao/src/__pycache__/x.pyc',
                    'CLAUDE.md', '.gitignore'):
            (top / rel).parent.mkdir(parents=True, exist_ok=True)
            (top / rel).write_text(rel)
        (top / '.gitignore').write_text('store\nCLAUDE.md\n__pycache__\n')
        git = ['git', '-C', str(top), '-c', 'user.name=t', '-c', 'user.email=t@example.invalid']
        subprocess.run(git + ['init', '-q'], check=True)
        subprocess.run(git + ['add', '.'], check=True)
        self.mnt = self.d / 'Volumes'
        self.mnt.mkdir()
        self.dedupe, STICK.DEDUPE_BYTES = STICK.DEDUPE_BYTES, 1024

    def tearDown(self):
        STICK.DEDUPE_BYTES = self.dedupe
        self.tmp.cleanup()

    def test_a_user_erases_through_sudo_and_gets_the_volumes(self):
        with tempfile.TemporaryDirectory() as d:
            host = FakeHost(d)
            host.euid = lambda: 501
            parts = STICK.partition(host, 'disk4', 1900)
            erase = [x for x in host.calls if 'partitionDisk' in x]
            self.assertEqual(erase[0][:2], ['sudo', '-n'])
            self.assertTrue(all(x[:2] != ['sudo', '-n'] or x[2] in ('diskutil', 'chown') for x in host.calls))
            self.assertEqual(set(parts), {'EFI', 'HAOBOOT', 'HAO'})
            host = FakeHost(d + '/root'); Path(d + '/root').mkdir()
            host.euid = lambda: 0
            STICK.partition(host, 'disk4', 1900)
            self.assertFalse([x for x in host.calls if x[0] == 'sudo'])      # as root: no sudo
            from unittest import mock
            host = FakeHost(d + '/image'); Path(d + '/image').mkdir()
            host.euid = lambda: 501
            with mock.patch.object(STICK, 'own_device', lambda disk: True):   # an image this user attached
                STICK.partition(host, 'disk4', 1900)
            self.assertFalse([x for x in host.calls if x[0] == 'sudo'])      # its own device: no sudo

    def test_a_user_mounts_the_erased_drives_efi_through_sudo(self):
        # issue 97: on a real drive the erase leaves EFI unmounted, and macOS mounts it only for root
        with tempfile.TemporaryDirectory() as d:
            host = FakeHost(d)
            host.euid = lambda: 501
            host.unmounted, host.efi_needs_root = {'disk4s1'}, True
            parts = STICK.partition(host, 'disk4', 1900)
            self.assertIn(['sudo', '-n', 'diskutil', 'mount', 'disk4s1'], host.calls)
            self.assertEqual(parts['EFI'][1], str(Path(d) / 'EFI'))
            from unittest import mock
            host = FakeHost(d + '/image'); Path(d + '/image').mkdir()
            host.euid = lambda: 501
            host.unmounted = {'disk4s1'}
            with mock.patch.object(STICK, 'own_device', lambda disk: True):   # an image this user attached
                STICK.partition(host, 'disk4', 1900)
            self.assertIn(['diskutil', 'mount', 'disk4s1'], host.calls)       # its own device: no sudo
            self.assertFalse([x for x in host.calls if x[0] == 'sudo'])

    def test_writes_both_partitions(self):
        host = FakeHost(self.mnt)
        steps = []
        rec = STICK.write(host, 'disk4', self.bs, self.esp, self.kit, self.top, record={'model': 'MacBookPro9,2'},
                          on_step=steps.append)
        self.assertEqual(host.calls[1][:3], ['diskutil', 'partitionDisk', '/dev/disk4'])
        self.assertEqual(host.calls[1][3:], ['GPT', 'MS-DOS', 'HAOBOOT', '%dM' % STICK.SLACK_MB, 'JHFS+', 'HAO', 'R'])
        self.assertEqual(host.calls[-1], ['diskutil', 'unmountDisk', '/dev/disk4'])
        esp, boot, kit = self.mnt / 'EFI', self.mnt / 'HAOBOOT', self.mnt / 'HAO'
        # OpenCore on the EFI partition as OCLP installs it: Bootstrap at CoreServices and EFI/BOOT, OpenCore in EFI/OC
        self.assertEqual((esp / 'System/Library/CoreServices/boot.efi').read_bytes(), b'boot')
        self.assertEqual((esp / 'EFI/BOOT/BOOTX64.EFI').read_bytes(), b'boot')
        self.assertEqual((esp / 'EFI/OC/OpenCore.efi').read_bytes(), b'MZ opencore')
        self.assertTrue((esp / 'EFI/OC/.contentFlavour').is_file())
        self.assertTrue((esp / 'EFI/OC/Kexts/Lilu.kext/Contents/Info.plist').is_file())
        self.assertFalse((boot / 'EFI').exists())
        self.assertEqual((boot / 'com.apple.recovery.boot/BaseSystem.dmg').read_bytes(), self.bs.read_bytes())
        c = plistlib.loads((esp / 'EFI/OC/config.plist').read_bytes())
        self.assertEqual(c['Misc']['Debug']['Target'], 0)
        # something on screen at once: OpenCore's picker, the Recovery its only entry, started after a few seconds
        self.assertEqual((c['Misc']['Boot']['ShowPicker'], c['Misc']['Boot']['Timeout']), (True, STICK.PICKER_TIMEOUT))
        self.assertEqual([x['Enabled'] for x in c['Misc']['Tools']], [False])
        self.assertEqual([(d['Path'], d['Enabled']) for d in c['UEFI']['Drivers']],
                         [('OpenCanopy.efi', True), ('ResetNvramEntry.efi', False)])
        self.assertEqual((boot / 'com.apple.recovery.boot/.contentDetails').read_text(), 'hao Installer')
        self.assertEqual(c['Misc']['Security']['DmgLoading'], 'Any')
        self.assertEqual(c['NVRAM']['Add'][NV]['boot-args'], 'keepsyms=1 -lilubetaall amfi=0x80')
        self.assertEqual(rec['boot_args'], 'keepsyms=1 -lilubetaall amfi=0x80')
        self.assertEqual(rec['model'], 'MacBookPro9,2')
        # hao without its store, git and caches; the kit, symlinks kept, the userspace twin a hard link
        self.assertTrue((kit / 'hao/tools/hao/hao').is_file())
        self.assertTrue((kit / 'hao/pipeline/lock.py').is_file())
        for gone in ('store', '.git', 'tools/hao/src/__pycache__', 'CLAUDE.md'):
            self.assertFalse((kit / 'hao' / gone).exists(), gone)
        self.assertEqual(os.readlink(kit / 'kit/py-x86_64/bin/python3'), 'python3.12')
        a, b = kit / 'kit/hooks/02-userspace/inputs/u/filesystem.dmg', kit / 'kit/hooks/15-identities/inputs/u/filesystem.dmg'
        self.assertEqual(a.stat().st_ino, b.stat().st_ino)
        self.assertNotEqual((kit / 'kit/hooks/15-identities/inputs/u/other.dmg').stat().st_ino, a.stat().st_ino)
        self.assertEqual(rec['hard_links'], [['kit/hooks/15-identities/inputs/u/filesystem.dmg',
                                              'kit/hooks/02-userspace/inputs/u/filesystem.dmg']])
        self.assertEqual(rec['kit_bytes'], sum(p.stat().st_size for p in self.kit.rglob('*')
                                               if p.is_file() and not p.is_symlink()) - 2048 - 4096)
        self.assertTrue((kit / 'kit/cryptex-cache/key.json').is_file())
        self.assertFalse((kit / 'kit/cryptex-cache/in').exists())
        # the launchers run hao with the kit's CPython
        for name, cmd in (('install', 'install --kit "$D/kit"'), ('report', 'report --output "$D/reports/')):
            text = (kit / name).read_text()
            self.assertTrue(os.access(kit / name, os.X_OK))
            self.assertIn('"$PY/bin/python3.12" -I -B "$D/hao/tools/hao/hao" ' + cmd, text)
        self.assertTrue((kit / 'reports').is_dir())
        self.assertIn('erased /dev/disk4', steps[0])
        self.assertEqual(len(steps), 4)

    def written(self, host):
        STICK.write(host, 'disk4', self.bs, self.esp, self.kit, self.top, record={'model': 'MacBookPro9,2'})
        return self.mnt / 'EFI', self.mnt / 'HAOBOOT', self.mnt / 'HAO'

    def test_rewrite_boot_only_touches_efi_and_haoboot(self):
        host = FakeHost(self.mnt)
        esp, boot, top = self.written(host)
        (esp / 'opencore-2026-11-16-132000.txt').write_text('00:000 00:000 OC: Starting OpenCore...')
        (esp / 'EFI/OC/stale.efi').write_bytes(b'old')
        kit_file = top / 'kit/hooks/02-userspace/inputs/u/filesystem.dmg'
        before = (kit_file.stat().st_ino, kit_file.stat().st_mtime_ns)
        dmg = boot / 'com.apple.recovery.boot/BaseSystem.dmg'
        dmg_mtime = dmg.stat().st_mtime_ns
        host.calls.clear()
        steps = []
        rec = STICK.rewrite_boot(host, 'disk4', self.esp, log=True, on_step=steps.append)
        self.assertFalse(any(c[:2] == ['diskutil', 'partitionDisk'] for c in host.calls))
        self.assertEqual(host.calls[-1], ['diskutil', 'unmountDisk', '/dev/disk4'])
        # the old log is kept on HAO, the ESP is OpenCore's tree again (nothing stale), logging on as asked
        self.assertEqual((top / 'reports/opencore-2026-11-16-132000.txt').read_text()[:6], '00:000')
        self.assertEqual(list(esp.glob('opencore-*.txt')), [])
        self.assertFalse((esp / 'EFI/OC/stale.efi').exists())
        c = plistlib.loads((esp / 'EFI/OC/config.plist').read_bytes())
        self.assertEqual(c['Misc']['Debug']['Target'], STICK.LOG_TARGET)
        # the same Recovery is not copied again; the kit is untouched
        self.assertEqual(dmg.stat().st_mtime_ns, dmg_mtime)
        self.assertEqual((kit_file.stat().st_ino, kit_file.stat().st_mtime_ns), before)
        saved = json.loads((top / 'stick.json').read_text())
        self.assertEqual(saved, rec)
        self.assertEqual((saved['opencore_logs_kept'], saved['opencore_log'], saved['model']),
                         (['opencore-2026-11-16-132000.txt'], STICK.LOG_TARGET, 'MacBookPro9,2'))
        self.assertIn('kept 1 OpenCore log in HAO/reports', steps[0])

    def test_rewrite_boot_with_a_new_recovery(self):
        host = FakeHost(self.mnt)
        esp, boot, top = self.written(host)
        new = self.d / 'BaseSystem-new.dmg'
        new.write_bytes(b'koly2' * 1000)
        rec = STICK.rewrite_boot(host, 'disk4', self.esp, new)
        self.assertEqual((boot / 'com.apple.recovery.boot/BaseSystem.dmg').read_bytes(), new.read_bytes())
        self.assertEqual(rec['basesystem_sha256'], STICK.sha256(new))

    def test_rewrite_boot_takes_opencore_from_the_kit_on_the_drive(self):
        host = FakeHost(self.mnt)
        esp, boot, top = self.written(host)
        with self.assertRaisesRegex(STICK.StickError, 'has no ESP'):
            STICK.rewrite_boot(host, 'disk4', None)
        kit_esp = top / STICK.KIT_ESP
        for rel, data in (('EFI/OC/OpenCore.efi', b'MZ kit opencore'), ('EFI/OC/config.plist', plistlib.dumps(oclp_config())),
                          ('System/Library/CoreServices/boot.efi', b'kit boot')):
            (kit_esp / rel).parent.mkdir(parents=True, exist_ok=True)
            (kit_esp / rel).write_bytes(data)
        STICK.write_manifest(top)                       # as create-installer would have written it with that ESP
        STICK.rewrite_boot(host, 'disk4')
        self.assertEqual((esp / 'EFI/OC/OpenCore.efi').read_bytes(), b'MZ kit opencore')
        self.assertEqual((esp / 'EFI/BOOT/BOOTX64.EFI').read_bytes(), b'kit boot')

    def test_rewrite_boot_never_writes_through_links_on_the_drive(self):
        host = FakeHost(self.mnt)
        esp, boot, top = self.written(host)
        outside = self.d / 'outside'; outside.mkdir()
        victim = outside / 'opencore-2026-11-16-132000.txt'; victim.write_text('host file')
        (esp / 'opencore-2026-11-16-132000.txt').write_text('payload')
        shutil.rmtree(top / 'reports'); os.symlink(outside, top / 'reports')     # reports -> a folder on the host
        with self.assertRaisesRegex(STICK.StickError, 'reports is not a folder'):
            STICK.rewrite_boot(host, 'disk4', self.esp)
        self.assertEqual(victim.read_text(), 'host file')
        os.unlink(top / 'reports'); (top / 'reports').mkdir()
        os.symlink(victim, top / 'reports' / 'opencore-2026-11-16-132000.txt')    # a link where the log goes
        STICK.rewrite_boot(host, 'disk4', self.esp)
        self.assertEqual(victim.read_text(), 'host file')                         # the link was replaced, not followed
        self.assertEqual((top / 'reports' / 'opencore-2026-11-16-132000.txt').read_text(), 'payload')
        info = top / 'stick.json'; real = info.read_text(); info.unlink(); os.symlink(victim, info)
        with self.assertRaisesRegex(STICK.StickError, 'stick.json is a link'):
            STICK.rewrite_boot(host, 'disk4', self.esp)
        self.assertEqual(victim.read_text(), 'host file')

    def test_write_new_is_exclusive_and_no_follow(self):
        target = self.d / 'out.json'
        STICK.write_new(target, b'one')
        self.assertEqual(target.read_bytes(), b'one')
        victim = self.d / 'victim'; victim.write_text('keep')
        os.unlink(target); os.symlink(victim, target)
        STICK.write_new(target, b'two')
        self.assertEqual((victim.read_text(), target.read_bytes(), target.is_symlink()), ('keep', b'two', False))

    def test_image_is_created_exclusively(self):
        host = FakeHost(self.mnt)
        victim = self.d / 'victim'; victim.write_text('keep')
        img = self.d / 'link.img'; os.symlink(victim, img)
        with self.assertRaises((STICK.StickError, FileExistsError)):
            STICK.attach_image(host, img, 10 ** 6)
        self.assertEqual(victim.read_text(), 'keep')

    def test_the_manifest_catches_any_change_on_the_drive(self):
        host = FakeHost(self.mnt)
        esp, boot, top = self.written(host)
        self.assertEqual(STICK.check_manifest(top), [])
        m = json.loads((top / STICK.MANIFEST).read_text())
        self.assertIn('install', m['files']); self.assertIn('kit/kit.json', m['files'])
        self.assertEqual(m['links']['kit/py-x86_64/bin/python3'], 'python3.12')
        (top / 'reports' / 'hao-report.txt').write_text('reports are not covered')
        self.assertEqual(STICK.check_manifest(top), [])
        launcher = (top / 'install').read_text()
        (top / 'install').write_text(launcher.replace('install', 'evil'))           # a casual edit
        (top / 'kit' / 'extra.sh').write_text('#!/bin/sh')                          # an added file
        os.unlink(top / 'kit/py-x86_64/bin/python3'); os.symlink('/bin/sh', top / 'kit/py-x86_64/bin/python3')
        os.chmod(top / 'report', 0o777)
        got = dict(STICK.check_manifest(top))
        self.assertIn('different', got['install'])
        self.assertEqual(got['kit/extra.sh'], 'a file that is not in the manifest')
        self.assertEqual(got['kit/py-x86_64/bin/python3'], 'the link points elsewhere')
        self.assertEqual(got['report'], 'a different mode')
        (top / STICK.MANIFEST).unlink()
        self.assertEqual(STICK.check_manifest(top)[0][0], STICK.MANIFEST)
        # rewrite-boot refuses a drive that no longer matches its manifest
        with self.assertRaisesRegex(STICK.StickError, 'differs from its manifest|manifest'):
            STICK.rewrite_boot(host, 'disk4', self.esp)

    def test_each_file_is_named_before_it_is_read(self):
        host = FakeHost(self.mnt)
        esp, boot, top = self.written(host)
        started, read = [], []
        self.assertEqual(STICK.check_manifest(top, on_file=lambda r, s: read.append(r),
                                              on_start=lambda r, s: started.append(r)), [])
        self.assertEqual(started, read)          # every file read was named first, in the same order
        self.assertIn('install', started)

    def test_manifest_holes_are_closed(self):
        host = FakeHost(self.mnt)
        esp, boot, top = self.written(host)
        # what running the installer writes is outside the check: logs, install work
        (top / 'logs').mkdir(); (top / 'logs' / 'install.log').write_text('x')
        (top / 'hao-install-20261117-090000').mkdir(); (top / 'hao-install-20261117-090000' / 'run.sh').write_text('x')
        self.assertEqual(STICK.check_manifest(top), [])
        # an AppleDouble-looking file inside the kit is not exempt (Python could read a ._x.pth); .DS_Store is
        (top / 'kit' / '.DS_Store').write_bytes(b'\x00\x00\x00\x01Bud1' + b'\x00' * 64)      # Finder's
        (top / 'hao' / '.DS_Store').write_text('import os')            # not Finder's: covered like any file
        self.assertEqual(dict(STICK.check_manifest(top)), {'hao/.DS_Store': 'a file that is not in the manifest'})
        os.unlink(top / 'hao' / '.DS_Store')
        (top / 'kit' / '._extra.pth').write_text('import os')
        self.assertEqual(dict(STICK.check_manifest(top)), {'kit/._extra.pth': 'a file that is not in the manifest'})
        os.unlink(top / 'kit' / '._extra.pth')
        # a malformed manifest is a problem, not a crash
        good = (top / STICK.MANIFEST).read_text()
        for bad in ('[]', 'null', '{"format": 1, "files": {"x": {}}, "links": {}}'):
            (top / STICK.MANIFEST).write_text(bad)
            self.assertEqual(STICK.check_manifest(top)[0][0], STICK.MANIFEST)
        (top / STICK.MANIFEST).write_text(good)
        # a link into what the manifest leaves out is refused when written
        os.symlink('../reports/x', top / 'kit' / 'into-reports')
        with self.assertRaisesRegex(STICK.StickError, 'links into reports'):
            STICK.write_manifest(top)

    def test_rewrite_boot_checks_the_drive_before_changing_it(self):
        host = FakeHost(self.mnt)
        esp, boot, top = self.written(host)
        rec = json.loads((top / 'stick.json').read_text()); rec['model'] = 'edited'
        (top / 'stick.json').write_text(json.dumps(rec))                # a casual edit of stick.json
        before = (esp / 'EFI/OC/config.plist').read_bytes()
        with self.assertRaisesRegex(STICK.StickError, 'stick.json'):
            STICK.rewrite_boot(host, 'disk4', self.esp, log=True)
        self.assertEqual((esp / 'EFI/OC/config.plist').read_bytes(), before)     # nothing on the drive changed

    def test_rewrite_boot_refuses_a_disk_hao_did_not_write(self):
        host = FakeHost(self.mnt)
        esp, boot, top = self.written(host)
        (top / 'stick.json').unlink()
        with self.assertRaisesRegex(STICK.StickError, 'not a hao USB installer'):
            STICK.rewrite_boot(host, 'disk4', self.esp)
        self.assertTrue((esp / 'EFI/OC/OpenCore.efi').is_file())

    def test_the_kit_on_the_drive_is_checked_file_by_file(self):
        host = FakeHost(self.mnt)
        rec = STICK.write(host, 'disk4', self.bs, self.esp, self.kit, self.top)
        self.assertEqual(rec['kit_files_verified'], 6)      # cryptex-cache/in left out, the python3 link not compared
        (self.mnt / 'HAO/kit/hooks/15-identities/inputs/u/other.dmg').write_bytes(b'short')
        with self.assertRaisesRegex(STICK.StickError, '1 files .*other.dmg'):
            STICK.verify_kit(self.kit, self.mnt / 'HAO/kit')

    def test_a_drive_that_lost_its_mount_is_not_written(self):
        host = FakeHost(self.mnt)
        parts = STICK.prepare(host, 'disk4', self.esp, self.bs)
        STICK.check_mounts(host, 'disk4', parts)                 # still the same partitions: fine
        same_number = dict(parts); same_number[STICK.KIT_NAME] = ('disk4s3', str(self.mnt / 'HAO'), 'OTHER-UUID')
        host.uuid = 'THIS-UUID'
        with self.assertRaisesRegex(STICK.StickError, 'no longer /dev/disk4s'):   # same numbers, another volume
            STICK.check_mounts(host, 'disk4', same_number)
        host.uuid = 'UUID-1'
        host.moved[str(self.mnt / 'HAO')] = 'disk9s2'            # another volume took HAO's place
        parts[STICK.KIT_NAME] = ('disk4s3', str(self.mnt / 'HAO'), 'UUID-1')
        (self.mnt / 'HAO').rename(self.mnt / 'gone')
        (self.mnt / 'HAO').mkdir()
        with self.assertRaisesRegex(STICK.StickError, 'no longer /dev/disk4s3'):
            STICK.write(host, 'disk4', self.bs, self.esp, self.kit, self.top, parts=parts)
        self.assertEqual(list((self.mnt / 'HAO').iterdir()), [])

    def test_a_disk_that_is_not_the_confirmed_one_is_not_erased(self):
        import disks as DISKS
        host = FakeHost(self.mnt)
        calls = []
        real = DISKS.recheck
        DISKS.recheck = lambda *a, **k: calls.append(a) or 'it is not the disk that was confirmed (media changed)'
        try:
            with self.assertRaisesRegex(STICK.StickError, 'not the disk that was confirmed'):
                STICK.partition(host, 'disk4', 300, expect={'identity': {'media': 'A'}})
        finally:
            DISKS.recheck = real
        self.assertEqual(len(calls), 1)
        self.assertFalse(any(c[:2] == ['diskutil', 'partitionDisk'] for c in host.calls))
        self.assertFalse(any('unmountDisk' in c for c in host.calls))

    def test_launcher_runs_python_from_memory(self):
        STICK.write(FakeHost(self.mnt), 'disk4', self.bs, self.esp, self.kit, self.top)
        text = (self.mnt / 'HAO' / 'install').read_text()
        self.assertIn('ditto "$PY" "$M"', text)
        self.assertLess(text.index('PY=$M'), text.index('exec "$PY/bin/python3.12"'))

    def test_the_generic_serial_has_a_year_and_week_macos_reads(self):
        from unittest import mock
        for _ in range(200):
            c = {'PlatformInfo': {'Generic': {}}}
            with mock.patch.object(STICK, 'is_generic_config', lambda c: True):
                g = STICK.generic_identity(c)
            s = g['SystemSerialNumber']
            self.assertEqual(len(s), 12)
            self.assertIn(s[3], STICK.SERIAL_YEARS)
            self.assertIn(s[4], STICK.SERIAL_WEEKS)

    def test_generic_esp_layout_without_coreservices(self):
        generic = self.d / 'generic-esp.tar'
        with tarfile.open(generic, 'w') as t:
            for name, data in (('EFI/OC/OpenCore.efi', b'MZ oc'), ('EFI/OC/config.plist', plistlib.dumps(oclp_config())),
                               ('EFI/BOOT/BOOTx64.efi', b'generic bootstrap')):
                info = tarfile.TarInfo(name); info.size = len(data); t.addfile(info, io.BytesIO(data))
        rec = STICK.write(FakeHost(self.mnt), 'disk4', self.bs, generic, self.kit, self.top)
        self.assertFalse((self.mnt / 'EFI/System').exists())
        self.assertEqual(rec['bootstrap_sha256'], STICK.sha256(STICK._found(self.mnt / 'EFI/EFI/BOOT', 'BOOTX64.EFI')))
        bad = self.d / 'no-bootstrap.tar'
        with tarfile.open(bad, 'w') as t:
            for name, data in (('EFI/OC/OpenCore.efi', b'MZ'), ('EFI/OC/config.plist', plistlib.dumps(oclp_config()))):
                info = tarfile.TarInfo(name); info.size = len(data); t.addfile(info, io.BytesIO(data))
        with self.assertRaisesRegex(STICK.StickError, 'no Bootstrap'):
            STICK.opencore_files(bad, self.d / 'esp2')

    def test_launcher_runs_hao_with_the_kits_python(self):
        host = FakeHost(self.mnt)
        STICK.write(host, 'disk4', self.bs, self.esp, self.kit, self.top)
        kit = self.mnt / 'HAO'
        machine = subprocess.run(['uname', '-m'], capture_output=True, text=True).stdout.strip()
        py = kit / 'kit' / ('py-x86_64' if machine == 'x86_64' else 'py') / 'bin'
        py.mkdir(parents=True, exist_ok=True)
        (py / 'python3.12').write_text('#!/bin/sh\necho "$@"\n')
        (py / 'python3.12').chmod(0o755)
        out = subprocess.run([str(kit / 'install'), '/dev/disk0'], capture_output=True, text=True, check=True).stdout
        self.assertEqual(out.split(), ['-I', '-B', str(kit / 'hao/tools/hao/hao'), 'install', '--kit', str(kit / 'kit'),
                                       '/dev/disk0'])

    def test_release_archive_without_git(self):
        import shutil
        shutil.rmtree(self.top / '.git')
        files = STICK.hao_files(self.top)
        self.assertIn('tools/hao/hao', files)
        self.assertIn('CLAUDE.md', files)            # an archive has none; what it holds is copied
        self.assertFalse([f for f in files if f.startswith(('store/', '.git/')) or '__pycache__' in f])

    def test_launcher_names_the_assembly_and_the_stick(self):
        STICK.write(FakeHost(self.mnt), 'disk4', self.bs, self.esp, self.kit, self.top,
                    record={'assembly': 'mbp92-26B131'})
        text = (self.mnt / 'HAO' / 'install').read_text()
        self.assertIn('install --kit "$D/kit" --assembly mbp92-26B131 "$@"', text)
        self.assertIn('export HAO_STICK=$D', text)
        self.assertIn('--output "$D/reports/hao-report-$(date +%Y%m%d-%H%M%S).txt" "$@"',
                      (self.mnt / 'HAO' / 'report').read_text())

    def test_report_hint_on_the_stick(self):
        env = dict(os.environ, HAO_STICK='/Volumes/HAO')
        code = 'import sys; sys.path.insert(0, %r); import installer; print(installer.REPORT)' % str(TOOL / 'src')
        out = subprocess.run([sys.executable, '-I', '-c', code], env=env, capture_output=True, text=True, check=True)
        self.assertIn('/Volumes/HAO/report', out.stdout)

    def test_refuses_a_folder_that_is_no_kit(self):
        (self.kit / 'kit.json').unlink()
        with self.assertRaises(STICK.StickError):
            STICK.write(FakeHost(self.mnt), 'disk4', self.bs, self.esp, self.kit, self.top)

    def test_boot_size_is_netrecs_formula(self):
        self.assertEqual(STICK.boot_mb(1462025716, 40 * STICK.MB), 1394 * 102 // 100 + 40 + 256)

    def test_attach_image_makes_a_sparse_raw_disk(self):
        host = FakeHost(self.mnt)
        img = self.d / 'out' / 'stick.img'
        self.assertEqual(STICK.attach_image(host, img, 64 * 10 ** 9), 'disk7')
        self.assertEqual(img.stat().st_size, 64 * 10 ** 9)
        self.assertLess(img.stat().st_blocks * 512, 10 ** 6)
        self.assertIn('diskimage-class=CRawDiskImage', host.calls[0])
        with self.assertRaises(STICK.StickError):
            STICK.attach_image(host, img, 10)

    def test_config_change_is_netrecs(self):
        """The stick's OpenCore change and pipeline/stages/netrec.sh's must not drift apart: run netrec's own
        Python (its heredoc) and ours on the same config."""
        text = (REPO / 'pipeline/stages/netrec.sh').read_text()
        script = re.search(r"python3 -I - \$W/mnt/EFI/BOOT/config.plist <<'EOF'\n(.*?)\nEOF\n", text, re.S).group(1)
        a, b = self.d / 'a.plist', self.d / 'b.plist'
        a.write_bytes(plistlib.dumps(oclp_config()))
        subprocess.run([sys.executable, '-I', '-', str(a)], input=script.encode(), check=True, capture_output=True)
        b.write_bytes(plistlib.dumps(STICK.opencore_config(oclp_config())))
        self.assertEqual(plistlib.loads(a.read_bytes()), plistlib.loads(b.read_bytes()))

    def generic_config(self):
        c = oclp_config()
        c['Kernel']['Add'].insert(1, {'BundlePath': 'VirtualSMC.kext', 'Enabled': True})
        c['NVRAM']['Add'][NV]['boot-args'] = '-v keepsyms=1 -no_compat_check debug=0x100'
        c['PlatformInfo'] = {'UpdateSMBIOSMode': 'Create', 'Generic': {
            'SystemProductName': 'MacBookPro9,2', 'SystemSerialNumber': '', 'MLB': '', 'SystemUUID': '', 'ROM': bytes(6)}}
        return c

    def test_generic_config_gets_lilubetaall_and_an_identity(self):
        """The generic USB installer's Recovery draws only with -lilubetaall (Lilu 1.7.2 + VirtualSMC in QEMU), and its
        blank machine identity is filled with random values, a new one per USB installer."""
        a = STICK.opencore_config(self.generic_config())
        b = STICK.opencore_config(self.generic_config())
        self.assertIn('-lilubetaall', a['NVRAM']['Add'][NV]['boot-args'].split())
        g, h = a['PlatformInfo']['Generic'], b['PlatformInfo']['Generic']
        self.assertEqual((len(g['SystemSerialNumber']), len(g['MLB']), len(g['ROM'])), (12, 17, 6))
        self.assertNotEqual(g['ROM'], bytes(6))
        self.assertNotEqual(g['SystemSerialNumber'], h['SystemSerialNumber'])
        self.assertNotEqual(g['SystemUUID'], h['SystemUUID'])

    def test_generic_identity_already_set_is_kept(self):
        c = self.generic_config()
        c['PlatformInfo']['Generic'].update(SystemSerialNumber='C0000000000X', SystemUUID='U')
        self.assertEqual(STICK.opencore_config(c)['PlatformInfo']['Generic']['SystemSerialNumber'], 'C0000000000X')

    def test_mac_config_untouched_by_the_generic_changes(self):
        c = oclp_config()
        c['NVRAM']['Add'][NV]['boot-args'] = 'keepsyms=1'
        out = STICK.opencore_config(c)
        self.assertNotIn('-lilubetaall', out['NVRAM']['Add'][NV]['boot-args'].split())
        self.assertNotIn('PlatformInfo', out)


if __name__ == '__main__':
    unittest.main()
