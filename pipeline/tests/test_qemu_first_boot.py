"""pipeline/qemu-first-boot with a fake host: build, export, the QEMU ESP, the boot, the checks and the records."""
from pathlib import Path
import json
import plistlib
import runpy
import subprocess
import tempfile
import unittest

TOP = Path(__file__).resolve().parents[2]
Q = runpy.run_path(str(TOP / 'pipeline' / 'qemu-first-boot'))
CFG = json.loads((TOP / 'pipeline' / 'qemu-boot.json').read_text())
PS = '  PID COMM\n    1 /sbin/launchd\n  106 /System/Library/PrivateFrameworks/SkyLight.framework/Resources/WindowServer\n' \
     '  112 /System/Library/CoreServices/loginwindow.app/Contents/MacOS/loginwindow\nREPORTS 2\n'


class FakeHost:
    def __init__(self, root, ssh_after=2, ps=PS, serial='', build_rc=0, slot=True):
        self.root, self.ssh_after, self.ps, self.serial, self.build_rc, self.slot = root, ssh_after, ps, serial, \
            build_rc, slot
        self.calls, self.probes = [], 0

    def disk(self, need_gb, what, path):
        self.calls.append(('disk', what))
        return None

    def vm_slot(self, limit):
        self.calls.append(('slot', limit))
        return self.slot

    def build(self, assembly, repo, log):
        self.calls.append(('build', assembly))
        repo.mkdir(parents=True)
        (repo / 'image.json').write_text(json.dumps({'size': 1000}))
        payload = self.root / 'payload-src' / 'payload' / 'EFI' / 'OC'
        for k in [CFG['commpage_kext']] + CFG['kexts']:
            (payload / 'Kexts' / k).mkdir(parents=True)
        (payload / 'config.plist').write_bytes(plistlib.dumps({'Kernel': {'Patch': [{'Comment': 'bound to BootKC'}]}}))
        (self.root / 'payload-src' / 'manifest.json').write_text('{}')
        subprocess.run(['zip', '-q', '-r', str(repo / 'boot-efi.zip'), 'payload', 'manifest.json'],
                       cwd=self.root / 'payload-src', check=True)
        return self.build_rc

    def newest_rev(self, repo):
        return 'abc1234'

    def export(self, repo, rev, dest, log):
        self.calls.append(('export', rev))
        dest.write_bytes(b'raw')
        return 0

    def row(self, build, name):
        self.calls.append(('row', build, name))
        return '/reference'

    def vm_esp(self, args, log):
        self.calls.append(('vm-esp', args))
        esp = Path(args[args.index('--out') + 1]) / 'EFI' / 'OC'
        esp.mkdir(parents=True)
        (esp / 'config.plist').write_bytes(plistlib.dumps({'Kernel': {'Patch': [{'Comment': 'reference'}],
                                                                      'Add': []}}))
        return 0

    def start(self, image, esp, run, name, port, memory, log):
        self.calls.append(('start', str(image), memory))
        run.mkdir()
        (run / 'serial.log').write_text(self.serial)
        (run / 'guest.qcow2').write_bytes(b'overlay')
        return 0

    def alive(self, run):
        return True

    def ssh(self, port, connect_timeout, command):
        if 'SSH-UP' in command:
            self.probes += 1
            return (0, 'SSH-UP\n') if self.probes > self.ssh_after else (255, '')
        return 0, self.ps

    def stop(self, run, log):
        self.calls.append(('stop',))
        return 0


class FirstBoot(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.root = Path(self.d.name).resolve()
        self.out = self.root / 'out'

    def tearDown(self):
        self.d.cleanup()

    def boot(self, host, *extra):
        clock = iter(range(0, 10 ** 6, 5))
        rc = Q['main'](['--out', str(self.out), '--commit', 'c0ffee00' * 5] + list(extra), host=host,
                       clock=lambda: next(clock), sleep=lambda s: None)
        return rc, json.loads((self.out / 'qemu-boot.json').read_text())

    def test_a_clean_first_boot_passes_and_is_recorded_twice(self):
        host = FakeHost(self.root)
        rc, rec = self.boot(host)
        self.assertEqual(rc, 0)
        self.assertEqual(rec['qemu_boot'], 'PASS')
        self.assertEqual(rec['rev'], 'abc1234')
        self.assertEqual(rec['reasons'], [])
        self.assertEqual(rec['diagnostic_reports'], 2)
        self.assertIn('WindowServer and loginwindow running', rec['marker'])
        self.assertEqual(set(rec['seconds']), {'build', 'export', 'esp', 'boot', 'check'})
        image = json.loads((self.out / 'image' / 'candidate.json').read_text())
        self.assertEqual((image['qemu_boot'], image['rev'], image['commit']), ('PASS', 'abc1234', 'c0ffee00' * 5))
        self.assertEqual(image['qemu_run'], str(self.out / 'boot'))
        # the raw export and the overlay are gone, the serial log kept
        self.assertFalse((self.out / 'target.img').exists())
        self.assertFalse((self.out / 'boot' / 'guest.qcow2').exists())
        self.assertTrue((self.out / 'boot' / 'serial.txt').exists())
        self.assertIn(('stop',), host.calls)
        self.assertEqual([c[0] for c in host.calls][:3], ['slot', 'build', 'disk'])

    def test_the_esp_is_made_from_the_image_with_its_kernel_patches(self):
        host = FakeHost(self.root)
        self.boot(host)
        args = next(c[1] for c in host.calls if c[0] == 'vm-esp')
        assembly = json.loads((TOP / 'pipeline/assemblies' / (CFG['assembly'] + '.json')).read_text())
        self.assertIn(('row', assembly['donor_build'], CFG['reference_row']), host.calls)
        self.assertEqual(args[args.index('--reference') + 1], '/reference')
        self.assertEqual(args[args.index('--darwin-major') + 1], str(CFG['darwin_major']))
        self.assertTrue(args[args.index('--commpage-kext') + 1].endswith('Kexts/' + CFG['commpage_kext']))
        self.assertEqual([args[i + 1] for i, x in enumerate(args) if x == '--boot-arg'], CFG['boot_args'])
        config = plistlib.loads((self.out / 'esp/EFI/OC/config.plist').read_bytes())
        self.assertEqual(config['Kernel']['Patch'], [{'Comment': 'bound to BootKC'}])
        self.assertIn('Add', config['Kernel'])

    def test_a_panic_fails(self):
        rc, rec = self.boot(FakeHost(self.root, ssh_after=10 ** 6, serial='panic(cpu 0 caller 0x1): x\n'))
        self.assertEqual(rc, 1)
        self.assertEqual(rec['qemu_boot'], 'FAIL')
        self.assertEqual(rec['outcome'], 'panic')
        self.assertFalse((self.out / 'target.img').exists())

    def test_a_missing_process_fails(self):
        rc, rec = self.boot(FakeHost(self.root, ps=PS.replace('loginwindow', 'other')))
        self.assertEqual(rc, 1)
        self.assertEqual(rec['reasons'], ['not running: loginwindow'])
        self.assertEqual(json.loads((self.out / 'image/candidate.json').read_text())['qemu_boot'], 'FAIL')

    def test_no_sshd_before_the_timeout_fails(self):
        rc, rec = self.boot(FakeHost(self.root, ssh_after=10 ** 6))
        self.assertEqual(rc, 1)
        self.assertEqual(rec['outcome'], 'timeout')

    def test_a_failed_build_boots_nothing(self):
        host = FakeHost(self.root, build_rc=1)
        rc, rec = self.boot(host)
        self.assertEqual(rc, 1)
        self.assertIn('golden-run failed', rec['reasons'][0])
        self.assertNotIn('start', [c[0] for c in host.calls])

    def test_no_free_vm_slot_builds_nothing(self):
        host = FakeHost(self.root, slot=False)
        rc, rec = self.boot(host)
        self.assertEqual(rc, 1)
        self.assertNotIn('build', [c[0] for c in host.calls])

    def test_no_build_boots_the_given_image(self):
        host = FakeHost(self.root)
        host.build(CFG['assembly'], self.root / 'given', None)
        host.calls = []
        rc, rec = self.boot(host, '--image', str(self.root / 'given'), '--no-build')
        self.assertEqual(rc, 0)
        self.assertNotIn('build', [c[0] for c in host.calls])
        self.assertTrue((self.root / 'given/candidate.json').exists())


class Helpers(unittest.TestCase):
    def test_macos_vms_counts_builder_vms_only(self):
        rows = ['/x/store/vm/bin/ssdvm run /x/vm/a --headless', 'ssdvm run /y', '/opt/homebrew/bin/qemu-system-x86_64 -m 8G',
                'grep ssdvm']
        self.assertEqual(Q['macos_vms'](rows), 2)

    def test_wait_vm_slot(self):
        counts = iter([2, 2, 1])
        self.assertTrue(Q['wait_vm_slot'](2, count=lambda: next(counts), clock=lambda: 0, sleep=lambda s: None))
        t = iter(range(0, 10 ** 6, 100))
        self.assertFalse(Q['wait_vm_slot'](2, count=lambda: 2, clock=lambda: next(t), sleep=lambda s: None,
                                           timeout=500))

    def test_missing_processes(self):
        self.assertEqual(Q['missing_processes'](PS, ['WindowServer', 'loginwindow', 'Dock']), ['Dock'])


if __name__ == '__main__':
    unittest.main()
