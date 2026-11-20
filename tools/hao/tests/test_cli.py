"""The end-user commands: console levels, hao detect, the disk gate, hao verify, hao fetch and the builder plans.

A FakeMac answers sysctl, ioreg, diskutil and df from Python data shaped like the MacBookPro9,2 test laptop (its
board id, HD 4000 0x0166, BCM4331 Wi-Fi and 7 Series HDA audio as OCLP's recorded profile of that model knows them)
plus disks of each kind; nothing runs a disk tool. The builder tests read the repository's own packs, assemblies and
set, and run only --dry-run, or stop at the check before any write.
"""
import argparse
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import plistlib
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = Path(__file__).resolve().parent
TOOL = HERE.parent
TOP = TOOL.parents[1]
sys.path.insert(0, str(TOOL / 'src'))
import console as CON   # noqa: E402
import detect as DETECT  # noqa: E402
import disks as DISKS   # noqa: E402
import verify as VERIFY  # noqa: E402
import builder as BUILD  # noqa: E402
import livepatch as LP  # noqa: E402


def pci(vendor, device, cls, name):
    le = lambda v: v.to_bytes(4, 'little')
    return {'vendor-id': le(vendor), 'device-id': le(device), 'class-code': le(cls), 'IOName': name}


LAPTOP_PCI = [pci(0x8086, 0x0166, 0x030000, 'display'), pci(0x14e4, 0x4331, 0x028000, 'pci14e4,4331'),
              pci(0x8086, 0x1e20, 0x040300, 'pci8086,1e20'), pci(0x8086, 0x1e31, 0x0c0330, 'pci8086,1e31')]
GiB = 1 << 30
DISKS_INFO = {
    'disk0': dict(WholeDisk=True, DeviceTreePath='IODeviceTree:/disk0', Internal=True, VirtualOrPhysical='Physical', BusProtocol='SATA', TotalSize=500 * 10**9,
                  MediaName='APPLE SSD SM512E'),
    'disk1': dict(WholeDisk=True, Internal=True, VirtualOrPhysical='Virtual', TotalSize=499 * 10**9),
    'disk4': dict(WholeDisk=True, DeviceTreePath='IODeviceTree:/disk4', Internal=False, VirtualOrPhysical='Physical', BusProtocol='USB', Removable=True,
                  TotalSize=32 * 10**9, MediaName='SanDisk Ultra'),
    'disk5': dict(WholeDisk=True, DeviceTreePath='IODeviceTree:/disk5', Internal=False, VirtualOrPhysical='Physical', BusProtocol='USB',
                  TotalSize=1000 * 10**9, MediaName='Samsung T7'),
    'disk6': dict(WholeDisk=True, DeviceTreePath='IODeviceTree:/disk6', Internal=False, VirtualOrPhysical='Physical', BusProtocol='USB',
                  TotalSize=16 * 10**9, MediaName='Small Stick'),
    'disk4s1': dict(WholeDisk=False, Internal=False),
}


class FakeMac:
    """sysctl/ioreg/diskutil/df answers; ROOT_DEVICE is where / lives; DF maps a source to its device."""

    def __init__(self, model='MacBookPro9,2', board=b'Mac-6F01561E16C75D06\0', machine='x86_64', devices=None,
                 root_device='disk1s5s1', uid=501, csr=0, boot_args=''):
        self.model, self.board, self.machine = model, board, machine
        self.uid, self.csr, self.boot_args = uid, csr, boot_args
        self.devices = LAPTOP_PCI if devices is None else devices
        self.root_device = root_device
        self.df = {}
        self.calls = []
        self.data_mount = None      # where the installed Data volume (disk1s2) is mounted, if anywhere

    def run(self, argv, check=True, timeout=120):
        argv = [str(a) for a in argv]
        self.calls.append(argv)
        out = self.answer(argv)
        if out is None:
            if check:
                raise LP.PatchError('%s failed' % ' '.join(argv))
            return subprocess.CompletedProcess(argv, 1, b'', b'')
        return subprocess.CompletedProcess(argv, 0, out, b'')

    def euid(self):
        return self.uid

    def csr_config(self):
        return self.csr

    def answer(self, argv):
        if argv == ['nvram', 'boot-args']:
            return ('boot-args\t%s\n' % self.boot_args).encode() if self.boot_args else None
        if argv[:2] == ['sysctl', '-n']:
            return {'hw.model': self.model, 'machdep.cpu.brand_string': 'Intel(R) Core(TM) i7-3615QM CPU @ 2.30GHz',
                    'hw.memsize': str(16 * GiB),
                    # the 3615QM (Ivy Bridge): AVX 1.0, no AVX2
                    'machdep.cpu.features': 'FPU VME SSE SSE2 SSE3 SSSE3 SSE4.1 SSE4.2 POPCNT AES AVX1.0 RDRAND F16C',
                    'machdep.cpu.leaf7_features': 'RDWRFSGS SMEP ERMS'}[argv[2]].encode()
        if argv[:2] == ['uname', '-m']:
            return self.machine.encode()
        if argv[0] == 'ioreg' and 'IODeviceTree' in argv and 'efi' in argv:
            vendor = getattr(self, 'firmware', 'Apple')
            return plistlib.dumps([{'firmware-vendor': (vendor + '\0').encode('utf-16-le')}] if vendor else [])
        if argv[0] == 'ioreg' and 'IOPlatformExpertDevice' in argv:
            return plistlib.dumps([{k: v for k, v in (('board-id', self.board), ('model', self.model.encode() + b'\0'))
                                    if v is not None}])
        if argv[0] == 'ioreg' and 'IOPCIDevice' in argv:
            return plistlib.dumps(self.devices)
        if argv[:3] == ['diskutil', 'info', '-plist']:
            if argv[3] == '/':
                return plistlib.dumps({'DeviceIdentifier': self.root_device})
            if argv[3] == 'disk1s2':
                return plistlib.dumps({'MountPoint': self.data_mount} if self.data_mount else {})
            info = DISKS_INFO.get(argv[3])
            if info is None and argv[3].endswith('s1'):
                info = {'MountPoint': '/Volumes/EFI'}
            return None if info is None else plistlib.dumps(dict(info, DeviceIdentifier=argv[3]))
        if argv[:3] == ['diskutil', 'list', '-plist']:      # the install's end: the target's EFI partition
            return plistlib.dumps({'AllDisksAndPartitions': [{'Partitions': [
                {'DeviceIdentifier': argv[3] + 's1', 'Content': 'EFI'}]}]})
        if argv[:2] == ['diskutil', 'mount'] or argv[0] == 'bless':
            return b''
        if argv[:4] == ['diskutil', 'apfs', 'list', '-plist']:
            return plistlib.dumps({'Containers': [
                {'ContainerReference': 'disk1', 'PhysicalStores': [{'DeviceIdentifier': 'disk0s2'}],
                 'Volumes': [{'DeviceIdentifier': 'disk1s1', 'Roles': ['System']},
                             {'DeviceIdentifier': 'disk1s2', 'Roles': ['Data']}]}]})

        if argv[:2] == ['df', '-P']:
            lines = ['Filesystem 512-blocks Used Available Capacity Mounted on']
            for s in argv[2:]:
                lines.append('/dev/%s 1 1 1 1%% %s' % (self.df.get(s, 'disk9s1'), s))
            return '\n'.join(lines).encode()
        raise AssertionError(argv)


def console(level='steps', answer='ERASE'):
    out = io.StringIO()
    return CON.Console(level, out=out, ask=lambda q: answer), out


def ns(**kw):
    base = dict(model=None, profile=None, assembly=None, dry_run=True, disk=None, confirm=None, quiet=False,
                show=False, show_edits=False, log=None, sources=[], no_restart=True)
    base.update(kw)
    return argparse.Namespace(**base)


class ConsoleTest(unittest.TestCase):
    def test_levels(self):
        for level, shows_steps, shows_files in (('quiet', False, False), ('steps', True, False),
                                                ('files', True, True), ('edits', True, True)):
            c, out = console(level)
            c.steps(2)
            c.step('Download')
            c.item('a file', 'files', ok=True)
            c.done('finished')
            text = out.getvalue()
            self.assertEqual('[1/2] Download' in text, shows_steps, level)
            self.assertEqual('a file' in text, shows_files, level)
            self.assertIn('finished', text)

    def test_json_is_events_only(self):
        c, out = console('json')
        c.steps(1)
        c.step('Seal')
        c.item('snapshot', ok=True, ev='snapshot', name='hao-base')
        c.done('ok')
        evs = [json.loads(line) for line in out.getvalue().splitlines()]
        self.assertEqual([e['ev'] for e in evs], ['step.start', 'snapshot', 'run.end'])
        self.assertEqual(evs[1]['name'], 'hao-base')
        self.assertEqual(c.events, evs)

    def test_flags(self):
        import argparse as ap
        p = ap.ArgumentParser()
        CON.add_level_flags(p)
        self.assertEqual(CON.level_of(p.parse_args([])), 'steps')
        self.assertEqual(CON.level_of(p.parse_args(['--show-edits'])), 'edits')
        self.assertEqual(CON.level_of(p.parse_args(['--log', 'json'])), 'json')
        with self.assertRaises(SystemExit), open(os.devnull, 'w') as null:
            sys.stderr, old = null, sys.stderr
            try:
                p.parse_args(['--quiet', '--show'])
            finally:
                sys.stderr = old

    def test_confirm(self):
        c, _ = console(answer='erase')
        self.assertFalse(c.confirm('Erase?', 'ERASE'))
        self.assertTrue(c.confirm('Erase?', 'ERASE', given='ERASE'))


class DetectTest(unittest.TestCase):
    def test_laptop(self):
        p = DETECT.profile(DETECT.facts(FakeMac()))
        self.assertEqual(p['board_id'], 'Mac-6F01561E16C75D06')
        self.assertTrue(p['patchsets_source'].startswith('recorded OCLP profile mbp92'))
        self.assertEqual(sorted(p['packs']), ['audio-modern', 'base', 'intel-hd4000', 'mbp92', 'metal-3802',
                                              'wifi-broadcom-modern'])
        self.assertTrue(p['proven_model'])
        self.assertEqual([m['role'] for m in p['matched']], ['gpu', 'wifi', 'audio'])
        self.assertEqual(p['notes'], [])
        # the same packs pipeline/patches.py detects for the target
        self.assertEqual(sorted(p['packs']), sorted(DETECT._patches()['detect_packs'](
            str(TOP / 'pipeline/targets/mbp92.json'))))

    def test_unrecorded_model_uses_rules(self):
        devices = LAPTOP_PCI[:1] + [pci(0x168c, 0x0030, 0x028000, 'atheros')]
        p = DETECT.profile(DETECT.facts(FakeMac(model='Macmini6,2', board=b'Mac-F65AE981FFA204ED\0',
                                                devices=devices)))
        self.assertEqual(p['patchsets_source'], 'device rules (tools/hao/data/detect-rules.json)')
        self.assertIn('intel-hd4000', p['packs'])
        self.assertNotIn('mbp92', p['packs'])
        self.assertNotIn('wifi-broadcom-modern', p['packs'])
        self.assertFalse(p['proven_model'])
        self.assertTrue(any('168c:0030' in n for n in p['notes']))

    def test_not_intel(self):
        p = DETECT.profile(DETECT.facts(FakeMac(model='Mac16,6', board=None, machine='arm64', devices=[])))
        self.assertEqual(p['packs'], [])

    def test_for_model_and_load(self):
        p = DETECT.for_model('MacBookPro9,2')
        self.assertEqual(p['target'], 'mbp92')
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / 'mac.json'
            f.write_text(json.dumps(DETECT.profile(DETECT.facts(FakeMac()))))
            self.assertEqual(DETECT.load(f)['target'], 'mbp92')
            f.write_text('{}')
            with self.assertRaises(DETECT.DetectError):
                DETECT.load(f)
        with self.assertRaises(DETECT.DetectError):
            DETECT.for_model('iMac13,2')


class DiskGateTest(unittest.TestCase):
    def check(self, disk, policy, minimum=20 * 10**9, **kw):
        host = FakeMac(**kw)
        return DISKS.refusal(host, disk, policy, minimum, sources=['/src'] if 'df' in kw else ())

    def test_external(self):
        self.assertIsNone(self.check('/dev/disk5', 'external'))
        self.assertIsNone(self.check('disk4', 'external'))
        self.assertIn('internal', self.check('disk0', 'external'))
        self.assertIn('virtual', self.check('disk1', 'external'))
        self.assertIn('too small', self.check('disk6', 'external'))
        self.assertIn('whole disk', self.check('disk4s1', 'external'))
        self.assertIn('whole disk name', self.check('/Volumes/X', 'external'))
        self.assertEqual(self.check('disk7', 'external'), 'diskutil info failed')

    def test_internal(self):
        self.assertIsNone(self.check('disk0', 'internal', root_device='disk9s1'))
        self.assertEqual(self.check('disk5', 'internal'), 'not internal')
        # the running system is on disk1, an APFS container on disk0: disk0 holds it
        self.assertEqual(self.check('disk0', 'internal'), 'holds the running system')

    def test_sources(self):
        host = FakeMac(root_device='disk9s1')
        host.df['/src'] = 'disk5s2'
        self.assertIn('own files', DISKS.refusal(host, 'disk5', 'external', 1, sources=['/src']))

    def test_reads_only(self):
        host = FakeMac()
        DISKS.refusal(host, 'disk5', 'external', 1)
        self.assertTrue(all(c[0] in ('diskutil', 'df') and c[1] in ('info', 'apfs', '-P') for c in host.calls))
        self.assertFalse(any(x in c for c in host.calls for x in ('eraseDisk', 'partitionDisk', 'unmountDisk')))


class CPUFeaturesTest(unittest.TestCase):
    """detect's CPU features: both sysctl keys, merged and sorted; a key the kernel lacks (Apple silicon) is skipped."""
    def test_merged_and_missing_keys_skipped(self):
        class Host:
            def run(self, argv, check=True, timeout=120):
                answers = {'machdep.cpu.features': b'sse avx1.0\n', 'machdep.cpu.leaf7_features': None}
                out = answers[argv[2]]
                if out is None:
                    raise LP.PatchError('unknown oid')
                return subprocess.CompletedProcess(argv, 0, out, b'')
        self.assertEqual(DETECT.cpu_features(Host()), ['AVX1.0', 'SSE'])


class VerifyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / 'root'
        self.ovl = Path(self.tmp.name) / 'overlays' / 'metal-3802'
        files = {'usr/lib/a.dylib': b'A', 'usr/lib/b.dylib': b'B', 'usr/lib/c.dylib': b'C'}
        owner = '%d:%d' % (os.getuid(), os.getgid())
        man = {'format': 1, 'pack': 'metal-3802', 'phase': 'live-capable', 'files': {}}
        for rel, data in files.items():
            (self.root / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.root / rel).write_bytes(data)
            os.chmod(self.root / rel, 0o755)
            man['files'][rel] = {'sha256': hashlib.sha256(data).hexdigest(), 'mode': '0755', 'owner': owner}
        self.ovl.mkdir(parents=True)
        (self.ovl / 'manifest.json').write_text(json.dumps(man))

    def tearDown(self):
        self.tmp.cleanup()

    def test_all_match(self):
        r = VERIFY.verify(self.root, VERIFY.manifests([self.ovl], self.root))
        self.assertEqual((r['ok'], r['problems']), (3, []))

    def test_problems(self):
        (self.root / 'usr/lib/a.dylib').write_bytes(b'changed')
        (self.root / 'usr/lib/b.dylib').unlink()
        os.chmod(self.root / 'usr/lib/c.dylib', 0o644)
        r = VERIFY.verify(self.root, VERIFY.manifests([self.ovl.parent], self.root))
        got = {p['path']: p['problem'] for p in r['problems']}
        self.assertEqual(got, {'usr/lib/a.dylib': 'content', 'usr/lib/b.dylib': 'missing',
                               'usr/lib/c.dylib': 'attributes'})

    def seeds(self, reason='Metal compiles append to it on first GPU use'):
        """a.dylib and b.dylib declared seeds ("content": "seed", "reason")."""
        man = json.loads((self.ovl / 'manifest.json').read_text())
        for rel in ('usr/lib/a.dylib', 'usr/lib/b.dylib'):
            man['files'][rel].update(content='seed', reason=reason)
        (self.ovl / 'manifest.json').write_text(json.dumps(man))

    def test_a_changed_seed_is_changed_as_expected(self):
        self.seeds()
        (self.root / 'usr/lib/a.dylib').write_bytes(b'A and more records')
        r = VERIFY.verify(self.root, VERIFY.manifests([self.ovl], self.root))
        self.assertEqual((r['ok'], r['problems']), (3, []))
        self.assertEqual([(x['path'], x['changed'], x['reason']) for x in r['changed']],
                         [('usr/lib/a.dylib', 'seed', 'Metal compiles append to it on first GPU use')])

    def test_a_seed_rewritten_with_another_mode_is_ok(self):
        # root's Metal cache comes back 0600 at first boot with the same bytes (v0.0.1, laptop 1): the mode is macOS's
        self.seeds()
        os.chmod(self.root / 'usr/lib/a.dylib', 0o600)
        r = VERIFY.verify(self.root, VERIFY.manifests([self.ovl], self.root))
        self.assertEqual((r['ok'], r['problems']), (3, []))

    def test_a_seed_still_fails_missing_or_with_other_flags_and_a_file_with_another_mode_fails(self):
        self.seeds()
        os.chmod(self.root / 'usr/lib/a.dylib', 0o600)
        os.chflags(self.root / 'usr/lib/a.dylib', stat.UF_NODUMP)
        (self.root / 'usr/lib/b.dylib').unlink()
        os.chmod(self.root / 'usr/lib/c.dylib', 0o600)      # not a seed: its mode is still checked
        r = VERIFY.verify(self.root, VERIFY.manifests([self.ovl], self.root))
        got = {p['path']: (p['problem'], p.get('keys')) for p in r['problems']}
        self.assertEqual(got, {'usr/lib/a.dylib': ('attributes', ['flags']), 'usr/lib/b.dylib': ('missing', None),
                               'usr/lib/c.dylib': ('attributes', ['mode'])})

    def test_hao_verify_prints_seeds_and_exits_0(self):
        self.seeds()
        (self.root / 'usr/lib/a.dylib').write_bytes(b'grown')
        r = subprocess.run([sys.executable, '-I', '-B', str(TOOL / 'hao'), 'verify', str(self.root), '--manifests',
                            str(self.ovl)], capture_output=True, text=True,
                           env=dict(os.environ, HAO_LOGS=self.tmp.name, HAO_ANYWHERE='1'))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn('changed as expected: Metal compiles append to it on first GPU use', r.stdout + r.stderr)
        self.assertIn('1 seed changed as expected', r.stdout + r.stderr)

    def test_installed_manifests_default(self):
        inst = self.root / VERIFY.INSTALLED
        inst.mkdir(parents=True)
        (inst / 'metal-3802.json').write_text((self.ovl / 'manifest.json').read_text())
        self.assertEqual(VERIFY.verify(self.root, VERIFY.manifests([], self.root))['ok'], 3)

    def test_no_manifests(self):
        with self.assertRaises(LP.PatchError):
            VERIFY.manifests([], self.root)

    def test_unbooted_install_reads_its_data_volume(self):
        # the Recovery mounts the System volume at ROOT and its Data volume at "ROOT - Data": the manifests and the
        # firmlinked files (here usr/lib/c.dylib stands for one) are there
        data = self.root.parent / (self.root.name + ' - Data')
        (data / 'usr/lib').mkdir(parents=True)
        os.replace(self.root / 'usr/lib/c.dylib', data / 'usr/lib/c.dylib')
        inst = data / VERIFY.INSTALLED
        inst.mkdir(parents=True)
        (inst / '06-metal.json').write_text((self.ovl / 'manifest.json').read_text())
        r = VERIFY.verify(self.root, VERIFY.manifests([], self.root))
        self.assertEqual((r['ok'], r['problems']), (3, []))
        self.assertIsNone(VERIFY.data_root('/'))


class GenericTree:
    """BUILD.PROFILES and BUILD.ASSEMBLIES of this checkout, which carries the generic image's profile and assembly
    (pipeline/profiles/generic.json, pipeline/assemblies/generic-26B131.json)."""

    def __enter__(self):
        for f in (BUILD.PROFILES / 'generic.json', BUILD.ASSEMBLIES / 'generic-26B131.json'):
            assert f.is_file(), f
        return self

    def __exit__(self, *exc):
        pass


class ImageProfileTest(unittest.TestCase):
    """--profile NAME: the image by its profile (pipeline/profiles), e.g. generic; --profile FILE stays hao detect's."""

    def build(self, **kw):
        c, out = console('files')
        ran = []
        a = ns(out='/nonexistent/repo', overlay_system=None, overlay_store=None, **kw)
        self.assertEqual(BUILD.build_image(a, c, run=ran.append), 0)
        self.assertEqual(ran, [])
        return a, c, out.getvalue()

    def test_build_image_profile_generic(self):
        with GenericTree():
            a, c, text = self.build(profile='generic')
        self.assertEqual(a.image_profile, 'generic')
        self.assertIn('generic-26B131.json --repo /nonexistent/repo', text)
        banner = next(e for e in c.events if e['ev'] == 'run.start')
        self.assertEqual((banner['assembly'], banner['model'], banner['packs']), ('generic-26B131', 'iMac20,1', ['base']))
        for platform in ('intel-hd4000', 'metal-3802', 'wifi-broadcom-modern', 'audio-modern', 'opencore-mbp92'):
            self.assertNotIn(platform, text)
        self.assertNotIn('os-extras', text)     # release profiles name no opt-in OS recipe (golden-gate-store is dev only)

    def test_build_image_profile_of_the_9_2(self):
        with GenericTree():
            _, _, text = self.build(profile='alpha-v0.1.0')
        self.assertIn('mbp92-26B131-alpha-v0.1.0.json --repo', text)
        self.assertIn('intel-hd4000', text)

    def test_default_is_unchanged(self):
        with GenericTree():
            a, _, text = self.build(model='MacBookPro9,2')
        self.assertIn('mbp92-26B131.json --repo', text)
        self.assertIsNone(getattr(a, 'image_profile', None))

    def test_unknown_name_and_detect_files(self):
        c, _ = console('quiet')
        with GenericTree(), self.assertRaises(CON.Stopped) as x:
            BUILD.resolve(ns(profile='nosuch'), c, 'build-image')
        self.assertIn('no image profile nosuch', x.exception.what)
        self.assertIn('generic', x.exception.options[0])
        with tempfile.TemporaryDirectory() as d:          # a hao detect file is still one, even named like a profile
            f = Path(d) / 'generic'
            f.write_text(json.dumps(DETECT.for_model('MacBookPro9,2')))
            a = ns(profile=str(f))
            with GenericTree():
                self.assertIsNone(BUILD.image_profile(a))
                self.assertEqual(BUILD.resolve(a, c, 'build-image')[1].stem, 'mbp92-26B131')

    def test_assembly_must_build_the_profile(self):
        c, _ = console('quiet')
        with GenericTree(), self.assertRaises(CON.Stopped) as x:
            BUILD.resolve(ns(profile='generic', assembly='mbp92-26B131'), c, 'build-image')
        self.assertIn('builds for target mbp92', x.exception.what)

    def test_the_generic_image_is_chosen_not_gated(self):
        c, _ = console('quiet')
        with GenericTree():
            for command in BUILD.GATED:
                a = ns(profile='generic')
                profile, path, asm, groups = BUILD.resolve(a, c, command)     # no stop: the choice is the consent
                self.assertEqual((path.stem, profile['target']), ('generic-26B131', 'generic'))
                self.assertTrue(a.unproven_reasons[0].startswith('the generic image generic'), a.unproven_reasons)
            # a Mac this release does not know, detected, making the generic image: the image's packs, not the Mac's
            a = ns(profile='generic')
            a.profile_data = dict(DETECT.profile(DETECT.facts(FakeMac(model='Macmini6,2', board=b'Mac-F65AE981FFA204ED\0'))),
                                  target=None)
            with self.assertRaises(CON.Stopped) as x:                            # a Mac: not without --unproven
                BUILD.resolve(a, c, 'create-installer')
            self.assertIn('not a Mac', x.exception.what)
            a.unproven = True
            profile, path, _, _ = BUILD.resolve(a, c, 'create-installer')
            self.assertEqual((profile['model'], profile['target'], profile['packs']), ('Macmini6,2', 'generic', ['base']))
            self.assertIn('Macmini6,2', a.unproven_reasons[0])
        # the 9,2 image on an unproven Mac still stops, and offers the generic image when there is one
        odd = DETECT.profile(DETECT.facts(FakeMac(model='MacBookPro9,1')))
        a = ns()
        a.generic_offer = 'generic'
        with self.assertRaises(CON.Stopped) as x:
            BUILD.proof_gate(a, c, odd, 'create-installer')
        self.assertEqual(x.exception.code, 5)
        self.assertIn('--profile generic', ' '.join(x.exception.options))


class BuilderTest(unittest.TestCase):
    def test_build_image_dry_run(self):
        c, out = console('files')
        ran = []
        a = ns(model='MacBookPro9,2', out='/nonexistent/repo', overlay_system=None, overlay_store=None)
        self.assertEqual(BUILD.build_image(a, c, run=ran.append), 0)
        self.assertEqual(ran, [])
        text = out.getvalue()
        self.assertIn('[2/3] Assemble in the builder VM', text)
        self.assertIn('mbp92-26B131.json --repo /nonexistent/repo', text)
        self.assertIn('metal-3802', text)

    def test_build_image_runs_golden_run(self):
        c, _ = console('quiet')
        ran = []
        a = ns(model='MacBookPro9,2', out='/r', overlay_system='/sys', overlay_store=None, dry_run=False,
               allow_unresolved_swaps=True)
        BUILD.build_image(a, c, run=lambda cmd: ran.append(cmd) or 0)
        self.assertEqual(ran[0][2:], [str(TOP / 'pipeline/golden-run'), str(TOP / 'pipeline/assemblies/mbp92-26B131.json'),
                                      '--repo', '/r', '--from-overlays', '--overlay-system', '/sys'])

    def test_build_image_stops_on_unresolved_swaps(self):
        with tempfile.TemporaryDirectory() as d:
            ledger = Path(d) / 'ledger.tsv'
            ledger.write_text('2026-11-13 10:00:00\t/usr/libexec/airportd\t%s\t755\t/src\tdev\n' % ('ab' * 32))
            old = os.environ.get('HAO_SWAP_LEDGER')
            os.environ['HAO_SWAP_LEDGER'] = str(ledger)
            try:
                c, out = console('quiet')
                ran = []
                a = ns(model='MacBookPro9,2', out='/r', overlay_system=None, overlay_store=None, dry_run=False)
                with self.assertRaises(CON.Stopped) as x:
                    BUILD.build_image(a, c, run=ran.append)
                self.assertIn('live-swapped', x.exception.what)
                self.assertEqual(ran, [])
                self.assertIn('/usr/libexec/airportd', out.getvalue())
            finally:
                if old is None:
                    os.environ.pop('HAO_SWAP_LEDGER', None)
                else:
                    os.environ['HAO_SWAP_LEDGER'] = old

    def test_build_disk_gate_and_dry_run(self):
        c, out = console('files')
        host = FakeMac(root_device='disk9s1')
        a = ns(model='MacBookPro9,2', disk='/dev/disk0')
        with self.assertRaises(CON.Stopped) as x:
            BUILD.build_disk(a, c, host)
        self.assertIn('external', x.exception.what)
        a = ns(model='MacBookPro9,2', disk='/dev/disk5')
        self.assertEqual(BUILD.build_disk(a, c, host), 0)
        self.assertIn('[10/10] Make it bootable', out.getvalue())

    def test_writes_stop_before_anything(self):
        host = FakeMac(root_device='disk9s1')
        ran = []
        # create-installer: tests/test_installer.py
        c, _ = console('quiet', answer='nope')
        a = ns(model='MacBookPro9,2', disk='disk5', dry_run=False)
        with self.assertRaises(CON.Stopped) as x:
            BUILD.build_disk(a, c, host, run=ran.append)
        self.assertEqual(x.exception.what, 'not confirmed')
        self.assertEqual(ran, [])
        self.assertFalse(any(x in c for c in host.calls for x in ('eraseDisk', 'partitionDisk', 'unmountDisk')))

    RELAXED = dict(uid=0, csr=0x803, boot_args='-no_compat_check amfi_get_out_of_my_way=1')

    def test_build_disk_refuses_an_everyday_mac(self):
        # confirmed, but not root / SIP on / AMFI strict: stops before staging or writing anything
        for env, why in ((dict(), 'root'), (dict(self.RELAXED, csr=0), 'filesystem protection'),
                         (dict(self.RELAXED, boot_args='-v'), 'AMFI')):
            host = FakeMac(root_device='disk9s1', **env)
            c, _ = console('quiet')
            ran = []
            a = ns(model='MacBookPro9,2', disk='disk5', dry_run=False, confirm='disk5')
            with self.assertRaises(CON.Stopped) as x:
                BUILD.build_disk(a, c, host, run=ran.append)
            self.assertIn(why, x.exception.what)
            self.assertIn('nothing was written', x.exception.untouched)
            self.assertEqual(ran, [])

    def fake_run(self, rc=0):
        ran = []
        def run(cmd):
            ran.append([str(x) for x in cmd])
            if '--stage-kit' in cmd:
                kit = Path(cmd[cmd.index('--work') + 1]) / 'share'
                kit.mkdir(parents=True)
                (kit / 'kit.json').write_text('{}')
            return rc if cmd[0] == 'sh' else 0
        return ran, run

    def test_build_disk_stages_assembles_external_and_collects(self):
        host = FakeMac(root_device='disk9s1', **self.RELAXED)
        with tempfile.TemporaryDirectory() as d:
            c, out = console('files')
            ran, run = self.fake_run()
            a = ns(model='MacBookPro9,2', disk='/dev/disk5', dry_run=False, confirm='disk5', work=d)
            self.assertEqual(BUILD.build_disk(a, c, host, run=run), 0)
            stage, assemble, collect = ran
            self.assertIn('--stage-kit', stage)
            self.assertEqual(assemble[0], 'sh')
            self.assertTrue(assemble[1].endswith('/share/assembly-source/golden-assemble.sh'))
            ident = assemble[assemble.index('--disk-identity') + 1]
            self.assertEqual(assemble[3:], [str(Path(d).resolve() / 'local'), '--disk', 'disk5',
                                            '--confirm-disk', 'disk5', '--disk-identity', ident, '--external'])
            # what the user confirmed, for golden-image.py to compare right before it erases, in a folder of its own
            self.assertEqual(json.loads(Path(ident).read_text()), DISKS.identity(host, 'disk5'))
            self.assertEqual(oct(Path(ident).parent.stat().st_mode & 0o777), '0o700')
            self.assertFalse(str(Path(ident).resolve()).startswith(str(Path(d).resolve())))
            self.assertIn('--collect-kit', collect)
            self.assertNotIn('--no-cryptex-cache', assemble)
            self.assertIn('built on /dev/disk5', out.getvalue())

    def test_build_disk_failed_assembly_stops(self):
        host = FakeMac(root_device='disk9s1', **self.RELAXED)
        with tempfile.TemporaryDirectory() as d:
            c, _ = console('quiet')
            ran, run = self.fake_run(rc=3)
            a = ns(model='MacBookPro9,2', disk='disk5', dry_run=False, confirm='disk5', work=d)
            with self.assertRaises(CON.Stopped) as x:
                BUILD.build_disk(a, c, host, run=run)
            self.assertIn('failed (exit 3)', x.exception.what)
            self.assertEqual(2, len(ran))   # staged and assembled; nothing collected

    def test_install_needs_a_kit_and_uses_it_on_the_internal_disk(self):
        host = FakeMac(root_device='disk9s1', **self.RELAXED)
        c, _ = console('quiet')
        with self.assertRaises(CON.Stopped) as x:
            BUILD.install(ns(disk='disk0', dry_run=False, confirm='ERASE'), c, host, run=lambda cmd: 0)
        self.assertEqual('no kit named', x.exception.what)
        with tempfile.TemporaryDirectory() as d:
            kit = Path(d) / 'kit'; kit.mkdir(); (kit / 'kit.json').write_text('{}')
            c, out = console('quiet')
            ran, run = self.fake_run()
            a = ns(disk='disk0', dry_run=False, confirm='ERASE', kit=str(kit))
            self.assertEqual(BUILD.install(a, c, host, run=run), 0)
            (assemble,) = ran
            self.assertEqual(assemble[2], str(kit.resolve()))
            self.assertEqual(assemble[-6:-2], ['--disk', 'disk0', '--confirm-disk', 'disk0'])
            self.assertEqual(assemble[-2], '--disk-identity')
            self.assertEqual(json.loads(Path(assemble[-1]).read_text()), DISKS.identity(host, 'disk0'))
            # nothing collects a stick's kit: the sealed cryptex images are not stored (they filled the stick)
            self.assertEqual(assemble[4], '--no-cryptex-cache')
            self.assertTrue(assemble[3].startswith(str(Path(d).resolve() / 'hao-install-')))
            # the start-up disk is set to OpenCore on the internal disk's EFI partition; --no-restart: no countdown
            self.assertIn(['bless', '--mount', '/Volumes/EFI', '--file',
                           '/Volumes/EFI/System/Library/CoreServices/boot.efi', '--setBoot'], host.calls)
            self.assertNotIn('restart.countdown', [e['ev'] for e in c.events])

    def test_generic_image_is_not_blessed(self):
        with GenericTree():
            host = FakeMac(root_device='disk9s1', **self.RELAXED)
            c, _ = console('quiet')
            self.assertTrue(BUILD.start_up(c, host, {'target': 'generic'}, 'disk0'))
            self.assertFalse([x for x in host.calls if x and x[0] == 'bless'])

    def test_install_writes_the_install_record_on_the_data_volume(self):
        host = FakeMac(root_device='disk9s1', **self.RELAXED)
        with tempfile.TemporaryDirectory() as d:
            kit = Path(d) / 'kit'; kit.mkdir(); (kit / 'kit.json').write_text('{"schema": 1}')
            host.data_mount = str(Path(d) / 'Data')
            c, out = console('files')
            ran, run = self.fake_run()
            self.assertEqual(BUILD.install(ns(disk='disk0', dry_run=False, confirm='ERASE', kit=str(kit)), c, host, run=run), 0)
            path = Path(d) / 'Data/private/var/db/hao/install-record.json'
            r = json.loads(path.read_text())
            self.assertEqual(oct(path.stat().st_mode & 0o777), '0o644')
            for key in ('date', 'release', 'golden_build', 'donor_build', 'model', 'board_id', 'packs', 'recipes', 'kit',
                        'disk'):
                self.assertIn(key, r)
            self.assertEqual((r['schema'], r['model'], r['disk'], r['command']), (1, 'MacBookPro9,2', 'disk0', 'install'))
            self.assertEqual(r['board_id'], 'Mac-6F01561E16C75D06')
            self.assertEqual(r['kit']['kit_json_sha256'], hashlib.sha256(b'{"schema": 1}').hexdigest())
            self.assertTrue(r['recipes'] and all(isinstance(v, int) for v in r['recipes'].values()), r['recipes'])
            self.assertEqual(sorted(sum(r['recipes_by_pack'].values(), [])), sorted(r['recipes']))
            self.assertIn('install record', out.getvalue())
            host.data_mount = None                  # no Data volume mounted (and mounting does not help): a warning
            c, _ = console('quiet')
            self.assertEqual(BUILD.install(ns(disk='disk0', dry_run=False, confirm='ERASE', kit=str(kit)), c, host, run=run), 0)

    def test_install_refuses_a_kit_or_work_folder_others_can_write(self):
        host = FakeMac(root_device='disk9s1', **self.RELAXED)
        with tempfile.TemporaryDirectory() as d:
            kit = Path(d) / 'shared' / 'kit'; kit.mkdir(parents=True); (kit / 'kit.json').write_text('{}')
            os.chmod(kit.parent, 0o777)
            ran, run = self.fake_run()
            c, _ = console('quiet')
            try:
                with self.assertRaises(CON.Stopped) as x:
                    BUILD.install(ns(disk='disk0', dry_run=False, confirm='ERASE', kit=str(kit)), c, host, run=run)
            finally:
                os.chmod(kit.parent, 0o755)
            self.assertIn('can be written by others', x.exception.what)
            self.assertEqual(ran, [])                    # the assembler never ran

    def test_install_refuses_a_work_folder_with_old_files(self):
        host = FakeMac(root_device='disk9s1', **self.RELAXED)
        with tempfile.TemporaryDirectory() as d:
            kit = Path(d) / 'kit'; kit.mkdir(); (kit / 'kit.json').write_text('{}')
            local = Path(d) / 'local'; local.mkdir(); (local / 'config.json').write_text('{}')
            ran, run = self.fake_run()
            c, _ = console('quiet')
            with self.assertRaises(CON.Stopped) as x:
                BUILD.install(ns(disk='disk0', dry_run=False, confirm='ERASE', kit=str(kit), local=str(local)), c, host,
                              run=run)
            self.assertIn('not a new, empty folder', x.exception.what)
            self.assertEqual(ran, [])

    def test_disk_identity_is_checked_again_before_the_assembler(self):
        host = FakeMac(root_device='disk9s1', **self.RELAXED)
        before = DISKS.identity(host, 'disk0')
        self.assertIsNone(DISKS.recheck(host, 'disk0', before, 'internal', 1))
        swapped = dict(before, media='Other SSD')
        self.assertIn('not the disk that was confirmed (media changed)',
                      DISKS.recheck(host, 'disk0', swapped, 'internal', 1))
        with tempfile.TemporaryDirectory() as d:
            kit = Path(d) / 'kit'; kit.mkdir(); (kit / 'kit.json').write_text('{}')
            ran, run = self.fake_run()
            real = DISKS.identity
            seen = []
            DISKS.identity = lambda h, disk: seen.append(disk) or (before if len(seen) == 1 else swapped)
            try:
                c, _ = console('quiet')
                with self.assertRaises(CON.Stopped) as x:
                    BUILD.install(ns(disk='disk0', dry_run=False, confirm='ERASE', kit=str(kit)), c, host, run=run)
            finally:
                DISKS.identity = real
            self.assertIn('was not erased', x.exception.what)
            self.assertEqual(ran, [])                    # the assembler never ran

    def test_install_checks_the_usb_installer_and_shows_the_statement_first(self):
        sys.path.insert(0, str(TOOL / 'src'))
        import stick as STICK
        host = FakeMac(root_device='disk9s1', **self.RELAXED)
        with tempfile.TemporaryDirectory() as d:
            top = Path(d) / 'HAO'; kit = top / 'kit'; kit.mkdir(parents=True)
            (kit / 'kit.json').write_text('{}'); (top / 'stick.json').write_text('{}'); (top / 'install').write_text('x')
            STICK.write_manifest(top)
            c, out = console('quiet')
            ran, run = self.fake_run()
            self.assertEqual(BUILD.install(ns(disk='disk0', dry_run=False, confirm='ERASE', kit=str(kit)), c, host, run=run), 0)
            text = out.getvalue()
            self.assertIn('Reduced security.', text)
            evs = [e['ev'] for e in c.events]
            self.assertLess(evs.index('drive.check'), evs.index('statement'))
            self.assertLess(evs.index('statement'), evs.index('confirm'))
            os.environ['HAO_STICK'] = str(top)                       # a kit elsewhere on the drive is not the covered one
            try:
                other = top / 'reports' / 'kit'; other.mkdir(parents=True); (other / 'kit.json').write_text('{}')
                c, _ = console('quiet')
                with self.assertRaises(CON.Stopped) as x:
                    BUILD.install(ns(disk='disk0', dry_run=False, confirm='ERASE', kit=str(other)), c, host, run=run)
                self.assertEqual(x.exception.code, 4)
            finally:
                del os.environ['HAO_STICK']
            (top / 'stick.json').unlink()                            # launched from the drive: the check is not optional
            os.environ['HAO_STICK'] = str(top)
            try:
                ran2, run2 = self.fake_run()
                c, _ = console('quiet')
                with self.assertRaises(CON.Stopped) as x:
                    BUILD.install(ns(disk='disk0', dry_run=False, confirm='ERASE', kit=str(kit)), c, host, run=run2)
                self.assertEqual((x.exception.code, ran2), (4, []))
            finally:
                del os.environ['HAO_STICK']
            (top / 'stick.json').write_text('{}')
            (top / 'install').write_text('changed')                  # the drive was edited: nothing is erased
            ran, run = self.fake_run()
            c, _ = console('quiet')
            with self.assertRaises(CON.Stopped) as x:
                BUILD.install(ns(disk='disk0', dry_run=False, confirm='ERASE', kit=str(kit)), c, host, run=run)
            self.assertEqual((x.exception.code, ran), (4, []))
            self.assertIn('install (a different size)', x.exception.what)

    # What `diskutil info -plist /Volumes/HAO/kit` answers in the USB installer's Recovery (byte for byte, QEMU
    # 2026-11-19; the HAO volume itself reports GlobalPermissionsEnabled false and is mounted noowners): a folder is no
    # disk, so there is no GlobalPermissionsEnabled, and 1bfb134b read that as "ownership on" and did nothing.
    DISKUTIL_INFO_FOLDER = (b'<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
                            b'"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n<plist version="1.0">\n<dict>\n'
                            b'\t<key>Error</key>\n\t<true/>\n\t<key>ErrorMessage</key>\n'
                            b'\t<string>Could not find disk: /Volumes/HAO/kit</string>\n\t<key>ExitCode</key>\n'
                            b'\t<integer>1</integer>\n</dict>\n</plist>\n')

    class OwnerHost:
        """diskutil as a Recovery answers it; enableOwnership turns the owner of new files from 99 to root when FIXES."""
        def __init__(self, owner, fixes=True):
            self.owner, self.fixes, self.calls = owner, fixes, []
        def run(self, argv, check=True, timeout=120):
            argv = [str(x) for x in argv]
            self.calls.append(argv)
            if argv[:2] == ['diskutil', 'info']:
                return subprocess.CompletedProcess(argv, 1, BuilderTest.DISKUTIL_INFO_FOLDER, b'')
            if argv[:2] == ['diskutil', 'enableOwnership'] and self.fixes:
                self.owner = 0
            return subprocess.CompletedProcess(argv, 0, b'', b'')
        def probe(self, folder):
            return self.owner
        def enabled(self):
            return [x for x in self.calls if x[:2] == ['diskutil', 'enableOwnership']]

    def test_install_turns_ownership_on_whatever_diskutil_info_says(self):
        with tempfile.TemporaryDirectory() as d:
            work = Path(d) / 'hao-install-20261119-200019'          # not made yet: tested in the folder above it
            off = self.OwnerHost(99)
            c, _ = console('quiet')
            self.assertTrue(BUILD.kit_ownership(c, off, work, owner=off.probe, euid=lambda: 0))
            self.assertEqual(off.enabled(), [['diskutil', 'enableOwnership', BUILD.mount_point(d)]])
            on = self.OwnerHost(0)
            self.assertFalse(BUILD.kit_ownership(c, on, work, owner=on.probe, euid=lambda: 0))   # already right
            self.assertEqual(on.enabled(), [])

    def test_install_stops_before_erasing_when_ownership_stays_off(self):
        with tempfile.TemporaryDirectory() as d:
            stuck = self.OwnerHost(99, fixes=False)
            c, _ = console('quiet')
            with self.assertRaises(CON.Stopped) as x:
                BUILD.kit_ownership(c, stuck, d, owner=stuck.probe, euid=lambda: 0)
            self.assertIn('uid 99', x.exception.what)
            self.assertEqual(x.exception.untouched, 'nothing was written to any disk')

    def test_install_stops_before_erasing_when_the_owner_test_cannot_run(self):
        host = FakeMac(root_device='disk9s1', **self.RELAXED)
        with tempfile.TemporaryDirectory() as d:
            kit = Path(d) / 'kit'; kit.mkdir(); (kit / 'kit.json').write_text('{}')
            ran, run = self.fake_run()
            c, _ = console('quiet')
            def fails(folder):
                raise PermissionError(13, 'Permission denied')
            with mock.patch.object(BUILD, 'new_file_owner', fails), self.assertRaises(CON.Stopped) as x:
                BUILD.install(ns(disk='disk0', dry_run=False, confirm='ERASE', kit=str(kit), work=str(Path(d) / 'w')),
                              c, host, run=run)
            self.assertIn('could not test who owns', x.exception.what)
            self.assertEqual(ran, [])                                    # nothing assembled, nothing erased

    def test_install_tests_the_owner_where_assembly_works(self):
        host = FakeMac(root_device='disk9s1', **self.RELAXED)
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as other:
            kit = Path(d) / 'kit'; kit.mkdir(); (kit / 'kit.json').write_text('{}')
            (Path(d) / 'w').symlink_to(other)                           # the work folder is a link elsewhere
            ran, run = self.fake_run()
            seen = []
            def probe(folder):
                seen.append(Path(folder))
                return os.geteuid()
            c, _ = console('quiet')
            with mock.patch.object(BUILD, 'new_file_owner', probe):
                BUILD.install(ns(disk='disk0', dry_run=False, confirm='ERASE', kit=str(kit), work=str(Path(d) / 'w')),
                              c, host, run=run)
            self.assertEqual(len(ran), 1)
            self.assertEqual(seen, [Path(other).resolve()])              # the link's target, where local/ will be made

    def test_new_file_owner_reads_a_real_file(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(BUILD.new_file_owner(d), os.geteuid())
            self.assertEqual(os.listdir(d), [])
            self.assertEqual(BUILD.mount_point(d), BUILD.mount_point(BUILD.mount_point(d)))
            self.assertTrue(os.path.ismount(BUILD.mount_point(d)))

    def test_install_resumes_a_failed_one(self):
        host = FakeMac(root_device='disk9s1', **self.RELAXED)
        with tempfile.TemporaryDirectory() as d:
            kit = Path(d) / 'kit'; kit.mkdir(); (kit / 'kit.json').write_text('{}')
            failed = Path(d) / 'hao-install-1' / 'local' / 'work'; failed.mkdir(parents=True)
            steps = [dict(name=n, complete=True) for n in ('layout', 'golden-system', 'group-volumes', 'golden-finalize')]
            (failed / 'build.json').write_text(json.dumps(dict(status='failed', steps=steps + [dict(name='boot-and-esp')])))
            ran, run = self.fake_run()
            a = ns(disk='disk0', dry_run=False, confirm='RESUME', kit=str(kit), resume=str(failed.parents[1]))
            c, out = console('steps')
            self.assertEqual(BUILD.install(a, c, host, run=run), 0)
            (assemble,) = ran
            self.assertEqual(assemble[-2:], ['--resume', str(failed.resolve())])
            self.assertIn('continues the install', out.getvalue())
            with self.assertRaises(CON.Stopped):    # ERASE does not confirm a resume
                BUILD.install(ns(disk='disk0', dry_run=False, confirm='ERASE', kit=str(kit), resume=str(failed)), c, host, run=run)
            steps.append(dict(name='boot-and-esp', complete=True)); steps += [dict(name='hardware', complete=True)]
            (failed / 'build.json').write_text(json.dumps(dict(status='failed', steps=steps + [dict(name='recipes')])))
            with self.assertRaises(CON.Stopped) as x:
                BUILD.install(ns(disk='disk0', dry_run=False, confirm='RESUME', kit=str(kit), resume=str(failed)), c, host, run=run)
            self.assertIn('stopped in recipes', x.exception.what)

    def test_failed_install_names_its_step_and_the_resume(self):
        host = FakeMac(root_device='disk9s1', **self.RELAXED)
        with tempfile.TemporaryDirectory() as d:
            kit = Path(d) / 'kit'; kit.mkdir(); (kit / 'kit.json').write_text('{}')
            work = Path(d) / 'w'

            def run(cmd):
                (work / 'local' / 'work').mkdir(parents=True)
                (work / 'local' / 'work' / 'build.json').write_text(json.dumps(dict(
                    status='failed', steps=[dict(name='layout', complete=True), dict(name='boot-and-esp')])))
                return 1
            c, _ = console('quiet')
            with self.assertRaises(CON.Stopped) as x:
                BUILD.install(ns(disk='disk0', dry_run=False, confirm='ERASE', kit=str(kit), work=str(work)), c, host, run=run)
            self.assertIn('in step boot-and-esp', x.exception.what)
            self.assertTrue(any('--resume %s' % work.resolve() in o for o in x.exception.options))

    def test_install_end_blesses_the_internal_esp(self):
        esp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__('shutil').rmtree(esp))
        listing = plistlib.dumps({'AllDisksAndPartitions': [{'Partitions': [
            {'DeviceIdentifier': 'disk0s1', 'Content': 'EFI'}, {'DeviceIdentifier': 'disk0s2', 'Content': 'Apple_APFS'}]}]})

        class Host:
            calls = []
            def run(self, argv, check=True, timeout=120):
                argv = [str(x) for x in argv]
                self.calls.append(argv)
                out = (listing if argv[:2] == ['diskutil', 'list'] else
                       plistlib.dumps({'MountPoint': str(esp)}) if argv[:2] == ['diskutil', 'info'] else b'')
                return subprocess.CompletedProcess(argv, 0, out, b'')
        host = Host()
        c, out = console('steps')
        self.assertTrue(BUILD.set_startup_disk(c, host, 'disk0'))
        self.assertEqual(host.calls[-1], ['bless', '--mount', str(esp), '--file',
                                          '%s/System/Library/CoreServices/boot.efi' % esp, '--setBoot'])
        self.assertIn('start-up disk', out.getvalue())
        c, out = console('steps')
        listing = plistlib.dumps({'AllDisksAndPartitions': [{'Partitions': []}]})
        self.assertFalse(BUILD.set_startup_disk(c, host, 'disk0'))    # no EFI partition: a warning, not a stop

    def test_no_restart_when_the_start_up_disk_was_not_set(self):
        host = FakeMac(root_device='disk9s1', **self.RELAXED)
        with tempfile.TemporaryDirectory() as d:
            kit = Path(d) / 'kit'; kit.mkdir(); (kit / 'kit.json').write_text('{}')
            ran, run = self.fake_run()
            real = BUILD.set_startup_disk
            BUILD.set_startup_disk = lambda c, h, disk: False
            try:
                c, out = console('quiet')
                a = ns(disk='disk0', dry_run=False, confirm='ERASE', kit=str(kit), no_restart=False)
                import unittest.mock as M
                with M.patch.object(sys.stdin, 'isatty', lambda: True):
                    self.assertEqual(BUILD.install(a, c, host, run=run), 0)
            finally:
                BUILD.set_startup_disk = real
            self.assertNotIn('restart.countdown', [e['ev'] for e in c.events])
            self.assertIn('restart to use it', out.getvalue())

    def test_restart_counts_down_and_a_key_cancels(self):
        class Host:
            calls = []
            def run(self, argv, check=True, timeout=120):
                self.calls.append(list(argv))
                return subprocess.CompletedProcess(argv, 0, b'', b'')
        host, c = Host(), console('steps')[0]
        self.assertTrue(BUILD.restart(c, host, lambda s: False, seconds=3))
        evs = [e['ev'] for e in c.events]
        self.assertEqual(evs, ['restart.countdown', 'restart.tick', 'restart.tick', 'restart.tick', 'restart.now'])
        self.assertEqual(host.calls, [['/sbin/reboot']])
        host.calls.clear()
        c, out = console('steps')
        presses = iter([False, True])
        self.assertFalse(BUILD.restart(c, host, lambda s: next(presses), seconds=3))
        self.assertEqual(c.events[-1], dict(c.events[-1], ev='restart.cancelled', left=2))
        self.assertEqual(host.calls, [])
        self.assertIn('Not restarting', out.getvalue())

    def test_install_record_names_a_dev_drives_commit(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / 'stick.json').write_text(json.dumps({'hao_commit': 'd02484f8aa' + '0' * 30}))
            old = os.environ.get('HAO_STICK'); os.environ['HAO_STICK'] = d
            try:
                release, commit, runtime = BUILD.release_of()
            finally:
                os.environ.pop('HAO_STICK') if old is None else os.environ.__setitem__('HAO_STICK', old)
            self.assertEqual((release, commit[:10], runtime), ('dev d02484f8', 'd02484f8aa', None))
            (Path(d) / 'release.json').write_text(json.dumps({'version': 'v0.0.1', 'hao_patcher': 'abc', 'hao_runtime': 'def'}))
            os.environ['HAO_STICK'] = d
            try:
                self.assertEqual(BUILD.release_of(), ('v0.0.1', 'abc', 'def'))
            finally:
                os.environ.pop('HAO_STICK') if old is None else os.environ.__setitem__('HAO_STICK', old)

    def test_unknown_or_unproven_mac_stops_unless_unproven(self):
        proven = DETECT.profile(DETECT.facts(FakeMac()))
        self.assertEqual(BUILD.unproven(proven), [])
        # a GPU no rule knows (a GT 650M in place of the HD 4000), and an unproven model
        odd = DETECT.profile(DETECT.facts(FakeMac(model='MacBookPro9,1', devices=[pci(0x10de, 0x0fd5, 0x030000, 'display')]
                                                                          + LAPTOP_PCI[1:])))
        self.assertTrue(odd['unknown'][0]['device'].startswith('10de:'), odd['unknown'])
        reasons = BUILD.unproven(odd)
        self.assertTrue(any(r.startswith('unknown GPU 10de:') for r in reasons), reasons)
        self.assertTrue(any('MacBookPro9,1' in r for r in reasons), reasons)
        for command in BUILD.GATED:
            c, _ = console('quiet')
            with self.assertRaises(CON.Stopped) as x:
                BUILD.proof_gate(ns(), c, odd, command)
            self.assertEqual(x.exception.code, 5)
            self.assertIn('--unproven', ' '.join(x.exception.options))
        c, _ = console('quiet')
        a = ns(unproven=True)
        BUILD.proof_gate(a, c, odd, 'install')               # allowed, and kept for the install record
        self.assertEqual(a.unproven_reasons, reasons)
        self.assertIn('unproven', [e['ev'] for e in c.events])
        a = ns()
        BUILD.proof_gate(a, c, odd, 'build-image')           # not an install: plan's own check (builder) applies
        self.assertEqual(a.unproven_reasons, [])

    def test_the_generic_image_always_needs_unproven(self):
        generic = {'model': 'QEMU Standard PC', 'packs': ['base', 'generic'], 'proven': {'base': True, 'generic': True},
                   'proven_model': True, 'unknown': []}
        self.assertEqual(BUILD.unproven(generic)[0], 'the generic image is an unsupported testing baseline')
        c, _ = console('quiet')
        with self.assertRaises(CON.Stopped) as x:
            BUILD.proof_gate(ns(), c, generic, 'create-installer')
        self.assertEqual((x.exception.code, x.exception.what), (5, 'the generic image needs --unproven'))
        c, out = console('quiet')
        a = ns(unproven=True)
        BUILD.proof_gate(a, c, generic, 'install')
        self.assertIn('unsupported baseline for testing', out.getvalue())
        self.assertIn('the generic image is an unsupported testing baseline', a.unproven_reasons)

    def test_generic_image_on_a_real_mac_says_so(self):
        mac = FakeMac(); vm = FakeMac(); vm.firmware = 'EDK II'
        self.assertEqual(DETECT.facts(mac)['firmware_vendor'], 'Apple')
        self.assertEqual(DETECT.facts(vm)['firmware_vendor'], 'EDK II')
        for host, is_mac in ((mac, True), (vm, False)):
            p = DETECT.profile(DETECT.facts(host)); p['packs'] = ['base', 'generic']
            reasons = BUILD.unproven(p)
            self.assertEqual(any('this is a Mac (Apple firmware)' in r for r in reasons), is_mac)
            c, out = console('quiet')
            with self.assertRaises(CON.Stopped) as x:                 # a real Mac and a VM: both need --unproven
                BUILD.proof_gate(ns(), c, p, 'install')
            self.assertEqual(x.exception.code, 5)
            self.assertIn('This generic image is for virtual machines and non-Apple PCs, not for Mac hardware.',
                          out.getvalue())
            c, _ = console('quiet')
            BUILD.proof_gate(ns(unproven=True), c, p, 'install')       # with --unproven it goes on (recorded)

    def test_unproven_install_is_in_the_record_and_the_launcher(self):
        c, _ = console('quiet')
        a = ns(kit=None)
        a.unproven_reasons = ['MacBookPro9,1 is not a proven model']
        rec = BUILD.install_record(a, c, {'model': 'MacBookPro9,1', 'packs': []}, Path('x.json'), {}, {}, 'disk0')
        self.assertEqual(rec['unproven'], ['MacBookPro9,1 is not a proven model'])
        sys.path.insert(0, str(TOOL / 'src'))
        import stick as STICK
        with tempfile.TemporaryDirectory() as d:
            kit = Path(d) / 'kit'; kit.mkdir(); (kit / 'kit.json').write_text('{}')
            top = Path(d) / 'top'; (top / 'tools/hao').mkdir(parents=True); (top / 'tools/hao/hao').write_text('')
            mount = Path(d) / 'HAO'; mount.mkdir()
            STICK.write_kit(mount, kit, top, record={'assembly': 'a1', 'unproven': ['x']})
            self.assertIn('--assembly a1 --unproven "$@"', (mount / 'install').read_text())
            mount2 = Path(d) / 'HAO2'; mount2.mkdir()
            STICK.write_kit(mount2, kit, top, record={'assembly': 'a1', 'unproven': []})
            self.assertNotIn('--unproven', (mount2 / 'install').read_text())

    def test_a_fresh_stick_passes_the_installs_own_check(self):
        # a new JHFS+ volume's root is group-writable (drwxrwxr-x); the Recovery's hao install refuses it
        sys.path.insert(0, str(TOOL / 'src'))
        import stick as STICK
        import safety as SAFE
        with tempfile.TemporaryDirectory() as d:
            os.chmod(d, 0o700)
            kit = Path(d) / 'kit'; (kit / 'sub').mkdir(parents=True); (kit / 'kit.json').write_text('{}')
            (kit / 'sub' / 'f').write_text('x'); os.chmod(kit / 'sub' / 'f', 0o664); os.chmod(kit / 'sub', 0o775)
            top = Path(d) / 'top'; (top / 'tools/hao').mkdir(parents=True); (top / 'tools/hao/hao').write_text('')
            mount = Path(d) / 'HAO'; mount.mkdir(); os.chmod(mount, 0o775)
            os.symlink('/tmp', mount / 'link')                   # links are left alone, never followed
            STICK.write_kit(mount, kit, top)
            self.assertEqual(oct(mount.stat().st_mode & 0o777), '0o755')
            self.assertIsNone(SAFE.others_can_change(mount, owners=False))
            self.assertIsNone(SAFE.others_can_change_tree(mount / 'kit', owners=False))
            self.assertTrue((mount / 'link').is_symlink())
            self.assertEqual(STICK.check_manifest(mount), [])   # the manifest records the closed modes

    def test_report_is_written_new_never_through_a_link(self):
        import report as REP
        with tempfile.TemporaryDirectory() as d:
            victim = Path(d) / 'victim'; victim.write_text('keep')
            out = Path(d) / 'hao-report.txt'; os.symlink(victim, out)
            with self.assertRaises(FileExistsError):
                REP.write(out, 'report text', env={})
            self.assertEqual(victim.read_text(), 'keep')
            fresh = Path(d) / 'new.txt'
            REP.write(fresh, 'report text', env={})
            # private to its owner (the security review: reports and logs are not left readable by others)
            self.assertEqual((fresh.read_text(), oct(fresh.stat().st_mode & 0o777)), ('report text', '0o600'))
            logs = Path(d) / 'logs'; logs.mkdir()
            os.symlink(victim, logs / 'install-20261117-000000.json')
            self.assertIsNone(REP.last_log([logs]))
        up = REP.scrub('HTTPS://alice:secret@example.invalid/f?token=secret2', {})
        self.assertNotIn('secret', up)
        with tempfile.TemporaryDirectory() as d:
            os.symlink('/etc/hosts', Path(d) / 'x.json')
            self.assertTrue(REP._json(Path(d) / 'x.json').startswith('unreadable'))
        text = REP.scrub('fetch https://user:pw@example.invalid/a.dmg?X-Amz-Signature=abc&t=1 ok', {})
        self.assertNotIn('pw', text); self.assertNotIn('abc', text)
        self.assertIn('https://[credentials]@example.invalid/a.dmg?[query] ok', text)

    def test_last_lines_take_the_backends_lines_and_the_new_install_text(self):
        hao = __import__('runpy').run_path(str(TOOL / 'hao'), run_name='hao_module')
        def last(command, text):
            out = io.StringIO()
            c = CON.Console('steps', out=out, colour=False)
            hao['finish_line'](c, command)
            c.done(text, more=['backend line'])
            return out.getvalue()
        self.assertIn('Restart to use it.', last('install', 'Installed macOS 27 on /dev/disk0; restart to use it'))
        text = last('install', 'Installed macOS 27 on /dev/disk0')        # the countdown follows: no restart hint
        self.assertTrue(text.startswith('Installed macOS 27 on /dev/disk0 in '), text)
        self.assertNotIn('Restart to use it.', text)
        self.assertIn('backend line', text)
        self.assertIn('backend line', last('create-installer', 'stick image written: /tmp/x'))

    def test_resumable_steps_are_the_assemblers(self):
        spec = importlib.util.spec_from_file_location('gi', BUILD.TOP / 'pipeline' / 'stages' / 'golden-image.py')
        gi = importlib.util.module_from_spec(spec); spec.loader.exec_module(gi)
        self.assertEqual(set(BUILD.RESUMABLE), set(gi.RESUMABLE))

    def test_install_detects_this_mac(self):
        c, out = console('edits')
        host = FakeMac(root_device='disk9s1')
        self.assertEqual(BUILD.install(ns(disk='disk0'), c, host), 0)
        text = out.getvalue()
        self.assertIn('intel-hd4000', text)
        self.assertIn('[7/7] Make it bootable', text)
        with self.assertRaises(CON.Stopped):
            BUILD.install(ns(disk='disk5'), c, host)

    def test_no_mac_named(self):
        c, _ = console()
        with self.assertRaises(CON.Stopped):
            BUILD.build_disk(ns(disk='disk5'), c, FakeMac())


class CommandLineTest(unittest.TestCase):
    def hao(self, *argv):
        return subprocess.run([sys.executable, '-I', '-B', str(TOOL / 'hao')] + list(argv), capture_output=True,
                              text=True)

    def test_detect_names_its_file_and_prints_no_json(self):
        with tempfile.TemporaryDirectory() as d:
            r = subprocess.run([sys.executable, '-I', '-B', str(TOOL / 'hao'), 'detect'], capture_output=True,
                               text=True, cwd=d)
            self.assertEqual(r.returncode, 0, r.stderr)
            (made,) = Path(d).glob('hao-profile-*.json')
            self.assertIn('Profile written to ', r.stdout); self.assertIn(made.name, r.stdout)
            self.assertNotIn('{', r.stdout)
            self.assertIn('model', json.loads(made.read_text()))

    def test_build_image_json(self):
        r = self.hao('build-image', '/nonexistent', '--model', 'MacBookPro9,2', '--dry-run', '--log', 'json')
        self.assertEqual(r.returncode, 0, r.stderr)
        evs = [json.loads(line) for line in r.stdout.splitlines()]
        self.assertEqual(evs[0]['ev'], 'run.start')
        self.assertEqual(evs[-1]['ev'], 'run.end')
        self.assertTrue(any(e['ev'] == 'command' for e in evs))

    def test_stopped_report(self):
        r = self.hao('build-image', '/x', '--dry-run')
        self.assertEqual(r.returncode, 1)
        self.assertIn('STOPPED', r.stderr)
        self.assertIn('--model', r.stderr)

    @unittest.skipUnless((TOOL.parents[1] / '.git').exists(), 'uses --unsigned, which only a checkout allows')
    def test_fetch_verify(self):
        with tempfile.TemporaryDirectory() as d:
            data = b'pinned'
            sha = hashlib.sha256(data).hexdigest()
            rel = Path(d) / 'release.json'
            rel.write_text(json.dumps({'downloads': [{'name': 'x', 'url': 'http://127.0.0.1:9/x', 'size': len(data),
                                                      'sha256': sha}]}))
            r = self.hao('fetch', '--unsigned', str(rel), '--cache', d + '/cache', '--verify')
            self.assertEqual(r.returncode, 4)       # a file failed its sha256 (frontdoor exit codes)
            (Path(d) / 'cache/sha256').mkdir(parents=True)
            (Path(d) / 'cache/sha256' / sha).write_bytes(data)
            r = self.hao('fetch', '--unsigned', str(rel), '--cache', d + '/cache', '--verify')
            self.assertEqual(r.returncode, 0, r.stderr)
            r = self.hao('fetch', '--unsigned', str(rel), '--cache', d + '/cache')     # present: reused, no request
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn('reused', r.stdout)

    @unittest.skipUnless((TOOL.parents[1] / '.git').exists(), 'uses --unsigned, which only a checkout allows')
    def test_fetch_release_assets_from_a_staged_folder(self):
        with tempfile.TemporaryDirectory() as d:
            stage, cache = Path(d) / 'stage', d + '/cache'
            stage.mkdir()
            apple, ours = b'apple file', b'our archive'
            (Path(d) / 'apple.bin').write_bytes(apple)
            (stage / 'shim.tar').write_bytes(ours)
            rel = stage / 'release.json'
            rel.write_text(json.dumps({
                'downloads': [{'row': 'ipsw', 'name': 'apple.bin', 'url': (Path(d) / 'apple.bin').as_uri(),
                               'size': len(apple), 'sha256': hashlib.sha256(apple).hexdigest()}],
                'assets': [{'row': 'shim', 'file': 'shim.tar', 'size': len(ours), 'format': 'tar', 'commit': 'c',
                            'url': 'http://127.0.0.1:9/27.2.1-26B131.0/shim.tar',
                            'sha256': hashlib.sha256(ours).hexdigest()}]}))
            r = self.hao('fetch', '--unsigned', str(rel), '--cache', cache, '--verify')
            self.assertEqual(r.returncode, 4)       # a file failed its sha256 (frontdoor exit codes)
            r = self.hao('fetch', '--unsigned', str(rel), '--cache', cache, '--assets-from', str(stage), '--log', 'json')
            self.assertEqual(r.returncode, 0, r.stderr)
            evs = [json.loads(line) for line in r.stdout.splitlines()]
            self.assertEqual(sorted(e['name'] for e in evs if e['ev'] == 'fetch.ok'), ['apple.bin', 'shim.tar'])
            self.assertEqual((Path(cache) / 'sha256' / hashlib.sha256(ours).hexdigest()).read_bytes(), ours)
            r = self.hao('fetch', '--unsigned', str(rel), '--cache', cache, '--verify')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn('1 downloads, 1 assets', r.stdout)
            (Path(cache) / 'sha256' / hashlib.sha256(ours).hexdigest()).write_bytes(b'changed')
            r = self.hao('fetch', '--unsigned', str(rel), '--cache', cache, '--verify')
            self.assertEqual(r.returncode, 4)       # a changed file fails its sha256
            self.assertIn('missing or damaged', r.stdout + r.stderr)


if __name__ == '__main__':
    unittest.main()
