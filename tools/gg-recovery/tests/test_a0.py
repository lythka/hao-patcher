"""Host fixtures for tools/gg-recovery: Mach-O slice matching, the OpenCore changes and the guest job. No VM, no disks."""
import importlib.machinery, importlib.util, plistlib, struct, sys, unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOOL = HERE.parent


def load(name, path, argv=None):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    mod = importlib.util.module_from_spec(spec)
    saved = sys.argv
    sys.argv = argv or [str(path)]
    try:
        loader.exec_module(mod)
    finally:
        sys.argv = saved
    return mod


guest = load('guest_a0', TOOL / 'src/guest_a0.py', ['guest_a0.py', '/nonexistent-share', '/nonexistent-work'])
a0 = load('gg_a0', TOOL / 'a0')

ARM64, ARM64E, X86_64 = 0x0100000c, 0x80000002, 0x01000007


def thin(cputype, subtype, body):
    return struct.pack('<III', 0xfeedfacf, cputype, subtype) + body.ljust(29, b'\0')


def fat(*parts):
    head, data, at = struct.pack('>II', 0xcafebabe, len(parts)), b'', 8 + 20 * len(parts)
    for ct, st, d in parts:
        head += struct.pack('>IIIII', ct, st, at + len(data), len(d), 0)
        data += d
    return head + data


class Slices(unittest.TestCase):
    def test_thin_and_fat(self):
        a = thin(ARM64, ARM64E, b'arm')
        self.assertEqual(guest.slices(a), [(ARM64, ARM64E, a)])
        x = thin(X86_64, 3, b'x86')
        self.assertEqual([s[:2] for s in guest.slices(fat((X86_64, 3, x), (ARM64, ARM64E, a)))],
                         [(X86_64, 3), (ARM64, ARM64E)])

    def test_not_macho(self):
        self.assertIsNone(guest.slices(b'#!/bin/sh\n' + b'\0' * 40))
        self.assertIsNone(guest.slices(struct.pack('>II', 0xcafebabe, 52) + b'\0' * 40))   # a Java class file

    def test_carried_only_when_every_slice_matches(self):
        a, x = thin(ARM64, ARM64E, b'arm'), thin(X86_64, 3, b'x86')
        universal = fat((X86_64, 3, x), (ARM64, ARM64E, a))
        self.assertTrue(guest.carried(a, universal))
        self.assertFalse(guest.carried(thin(ARM64, ARM64E, b'other'), universal))   # a different arm64e build
        self.assertFalse(guest.carried(a, fat((ARM64, ARM64E, a))))                 # no x86_64 slice
        self.assertFalse(guest.carried(fat((ARM64, 0, thin(ARM64, 0, b'v8')), (ARM64, ARM64E, a)), universal))


class Esp(unittest.TestCase):
    def test_configure(self):
        c = {'Misc': {'Security': {'DmgLoading': 'Signed'}, 'Boot': {'HideAuxiliary': True, 'Timeout': 15}}}
        a0.configure(c)
        self.assertEqual(c['Misc']['Security']['DmgLoading'], 'Any')
        self.assertFalse(c['Misc']['Boot']['HideAuxiliary'])
        self.assertEqual(c['Misc']['Boot']['Timeout'], 5)


class Job(unittest.TestCase):
    def test_job_formats_only_the_workspace_then_builds(self):
        s = a0.job_script('20261113-000000-abcdef')
        self.assertIn('golden-workspace.py" --size-bytes %d' % a0.WORKSPACE_BYTES, s)
        self.assertIn('guest_a0.py" "$G" /Volumes/gga0-20261113-000000-abcdef', s)
        self.assertLess(s.index('golden-workspace.py'), s.index('guest_a0.py'))
        self.assertTrue(s.rstrip().endswith('mv "$G/out/result.new" "$G/out/result"'))


class Access(unittest.TestCase):
    def test_daemons(self):
        for n in ('local.a0.marker.plist', 'local.a0.ssh.plist'):
            p = plistlib.loads((TOOL / 'src/access' / n).read_bytes())
            self.assertEqual(p['Label'], n.removesuffix('.plist'))
        cfg = (TOOL / 'src/access/sshd_config').read_text()
        self.assertIn('PasswordAuthentication no', cfg)
        self.assertIn('/usr/local/a0/authorized_keys', cfg)


if __name__ == '__main__':
    unittest.main()
