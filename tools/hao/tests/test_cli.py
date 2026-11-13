"""The end-user commands: console levels, hao detect, the disk gate, hao verify, hao fetch and the builder plans.

A FakeMac answers sysctl, ioreg, diskutil and df from Python data shaped like the MacBookPro9,2 test laptop (its
board id, HD 4000 0x0166, BCM4331 Wi-Fi and 7 Series HDA audio as OCLP's recorded profile of that model knows them)
plus disks of each kind; nothing runs a disk tool. The builder tests read the repository's own packs, assemblies and
set, and run only --dry-run, or stop at the check before any write.
"""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
import unittest

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
    'disk0': dict(WholeDisk=True, Internal=True, VirtualOrPhysical='Physical', BusProtocol='SATA', TotalSize=500 * 10**9,
                  MediaName='APPLE SSD SM512E'),
    'disk1': dict(WholeDisk=True, Internal=True, VirtualOrPhysical='Virtual', TotalSize=499 * 10**9),
    'disk4': dict(WholeDisk=True, Internal=False, VirtualOrPhysical='Physical', BusProtocol='USB', Removable=True,
                  TotalSize=32 * 10**9, MediaName='SanDisk Ultra'),
    'disk5': dict(WholeDisk=True, Internal=False, VirtualOrPhysical='Physical', BusProtocol='USB',
                  TotalSize=1000 * 10**9, MediaName='Samsung T7'),
    'disk6': dict(WholeDisk=True, Internal=False, VirtualOrPhysical='Physical', BusProtocol='USB',
                  TotalSize=16 * 10**9, MediaName='Small Stick'),
    'disk4s1': dict(WholeDisk=False, Internal=False),
}


class FakeMac:
    """sysctl/ioreg/diskutil/df answers; ROOT_DEVICE is where / lives; DF maps a source to its device."""

    def __init__(self, model='MacBookPro9,2', board=b'Mac-6F01561E16C75D06\0', machine='x86_64', devices=None,
                 root_device='disk1s5s1'):
        self.model, self.board, self.machine = model, board, machine
        self.devices = LAPTOP_PCI if devices is None else devices
        self.root_device = root_device
        self.df = {}
        self.calls = []

    def run(self, argv, check=True, timeout=120):
        argv = [str(a) for a in argv]
        self.calls.append(argv)
        out = self.answer(argv)
        if out is None:
            if check:
                raise LP.PatchError('%s failed' % ' '.join(argv))
            return subprocess.CompletedProcess(argv, 1, b'', b'')
        return subprocess.CompletedProcess(argv, 0, out, b'')

    def answer(self, argv):
        if argv[:2] == ['sysctl', '-n']:
            return {'hw.model': self.model, 'machdep.cpu.brand_string': 'Intel(R) Core(TM) i7-3615QM CPU @ 2.30GHz',
                    'hw.memsize': str(16 * GiB)}[argv[2]].encode()
        if argv[:2] == ['uname', '-m']:
            return self.machine.encode()
        if argv[0] == 'ioreg' and 'IOPlatformExpertDevice' in argv:
            return plistlib.dumps([{k: v for k, v in (('board-id', self.board), ('model', self.model.encode() + b'\0'))
                                    if v is not None}])
        if argv[0] == 'ioreg' and 'IOPCIDevice' in argv:
            return plistlib.dumps(self.devices)
        if argv[:3] == ['diskutil', 'info', '-plist']:
            if argv[3] == '/':
                return plistlib.dumps({'DeviceIdentifier': self.root_device})
            info = DISKS_INFO.get(argv[3])
            return None if info is None else plistlib.dumps(dict(info, DeviceIdentifier=argv[3]))
        if argv[:4] == ['diskutil', 'apfs', 'list', '-plist']:
            return plistlib.dumps({'Containers': [
                {'ContainerReference': 'disk1', 'PhysicalStores': [{'DeviceIdentifier': 'disk0s2'}]}]})
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
                show=False, show_edits=False, log=None, sources=[])
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

    def test_installed_manifests_default(self):
        inst = self.root / VERIFY.INSTALLED
        inst.mkdir(parents=True)
        (inst / 'metal-3802.json').write_text((self.ovl / 'manifest.json').read_text())
        self.assertEqual(VERIFY.verify(self.root, VERIFY.manifests([], self.root))['ok'], 3)

    def test_no_manifests(self):
        with self.assertRaises(LP.PatchError):
            VERIFY.manifests([], self.root)


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
        a = ns(model='MacBookPro9,2', out='/r', overlay_system='/sys', overlay_store=None, dry_run=False)
        BUILD.build_image(a, c, run=lambda cmd: ran.append(cmd) or 0)
        self.assertEqual(ran[0][2:], [str(TOP / 'pipeline/golden-run'), str(TOP / 'pipeline/assemblies/mbp92-26B131.json'),
                                      '--repo', '/r', '--from-overlays', '--overlay-system', '/sys'])

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
        for fn, disk in ((BUILD.build_disk, 'disk5'), (BUILD.create_installer, 'disk4')):
            c, _ = console('quiet', answer='nope')
            a = ns(model='MacBookPro9,2', disk=disk, dry_run=False, minimum_bytes=10**9)
            with self.assertRaises(CON.Stopped) as x:
                fn(a, c, host)
            self.assertEqual(x.exception.what, 'not confirmed')
            c, _ = console('quiet')
            a.confirm = 'disk5' if disk == 'disk5' else 'ERASE'
            with self.assertRaises(CON.Stopped) as x:
                fn(a, c, host)
            self.assertIn('cannot write yet', x.exception.what)
        self.assertFalse(any(x in c for c in host.calls for x in ('eraseDisk', 'partitionDisk', 'unmountDisk')))

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

    def test_fetch_verify(self):
        with tempfile.TemporaryDirectory() as d:
            data = b'pinned'
            sha = hashlib.sha256(data).hexdigest()
            rel = Path(d) / 'release.json'
            rel.write_text(json.dumps({'downloads': [{'name': 'x', 'url': 'http://127.0.0.1:9/x', 'size': len(data),
                                                      'sha256': sha}]}))
            r = self.hao('fetch', str(rel), '--cache', d + '/cache', '--verify')
            self.assertEqual(r.returncode, 1)
            (Path(d) / 'cache/sha256').mkdir(parents=True)
            (Path(d) / 'cache/sha256' / sha).write_bytes(data)
            r = self.hao('fetch', str(rel), '--cache', d + '/cache', '--verify')
            self.assertEqual(r.returncode, 0, r.stderr)
            r = self.hao('fetch', str(rel), '--cache', d + '/cache')     # present: reused, no request
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn('reused', r.stdout)


if __name__ == '__main__':
    unittest.main()
