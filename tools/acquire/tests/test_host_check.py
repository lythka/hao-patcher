"""host-check: each builder step's needs, on synthetic hosts. python3.14 -I -B -m unittest discover -s tools/acquire/tests"""
from pathlib import Path
import runpy
import unittest

H = runpy.run_path(str(Path(__file__).resolve().parents[1] / 'host-check'))
BASE = {'macos': '13.6', 'macos_major': 13, 'arch': 'x86_64', 'python': '3.11', 'free_gb': 500, 'need_gb': 60,
        'rosetta': True, 'codesign': True, 'hdiutil': True, 'pkgutil': True, 'aea': True, 'ipsw': False,
        'xcrun_metal': False, 'llvm_bundle': False, 'root': False}


class HostCheck(unittest.TestCase):
    def steps(self, **over):
        return {n: (ok, why) for n, ok, why in H['steps'](dict(BASE, **over))}

    def test_ventura_intel_without_xcode_needs_a_metal_toolchain(self):
        s = self.steps()
        self.assertFalse(s['rebuild Metal libraries (legacy-Metal targets)'][0])
        self.assertIn('bundled LLVM', s['rebuild Metal libraries (legacy-Metal targets)'][1])
        self.assertTrue(self.steps(llvm_bundle=True)['rebuild Metal libraries (legacy-Metal targets)'][0])

    def test_monterey_without_aea_needs_ipsw(self):
        self.assertFalse(self.steps(aea=False)['decode the macOS 27 IPSW (AEA)'][0])
        self.assertTrue(self.steps(aea=False, ipsw=True)['decode the macOS 27 IPSW (AEA)'][0])

    def test_space_and_root(self):
        self.assertIn('free space', self.steps(free_gb=20)['download and verify (tools/acquire)'][1])
        self.assertEqual(self.steps()['write the USB installer'], (False, 'root (sudo)'))


if __name__ == '__main__':
    unittest.main()
