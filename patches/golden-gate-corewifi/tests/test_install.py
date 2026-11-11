"""Host tests for golden-gate-corewifi's installation (src/install.py, src/kc-hook.sh) on an authored System root:
exactly the two files land in CoreWiFi.framework/Versions/A, nothing else changes (airportd keeps its bytes), the
inputs' install names and re-export are checked, and an existing different CoreWiFi binary or a missing framework
folder stops the install. Mach-O inputs are tiny x86_64 dylibs built here. Root-only steps (owner, flags) are skipped
when not root. Run: python3.14 -I -B -m unittest discover -s patches/golden-gate-corewifi/tests"""
import hashlib
import json
from pathlib import Path
import runpy
import subprocess
import tempfile
import unittest

PATCH = Path(__file__).resolve().parents[1]
INSTALL = runpy.run_path(str(PATCH/'src/install.py'), run_name='corewifi_install')
FRAMEWORK = '/System/Library/PrivateFrameworks/CoreWiFi.framework/Versions/A'


def sh(*args):
    return subprocess.run(list(map(str, args)), check=True, capture_output=True, text=True).stdout


def tree(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(Path(root).rglob('*')) if p.is_file()}


class Install(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix='corewifi-install-test-'))
        (cls.tmp/'a.c').write_text('int corewifi_tahoe_stand_in;\n')
        (cls.tmp/'b.c').write_text('int corewifi_compat_stand_in;\n')
        cls.tahoe, cls.compat = cls.tmp/'CoreWiFiTahoe.dylib', cls.tmp/'CoreWiFi'
        sh('xcrun', 'clang', '-arch', 'x86_64', '-dynamiclib', '-install_name', f'{FRAMEWORK}/CoreWiFiTahoe.dylib',
           cls.tmp/'a.c', '-o', cls.tahoe)
        sh('xcrun', 'clang', '-arch', 'x86_64', '-dynamiclib', '-install_name', f'{FRAMEWORK}/CoreWiFi',
           f'-Wl,-reexport_library,{cls.tahoe}', cls.tmp/'b.c', '-o', cls.compat)
        cls.plain = cls.tmp/'plain.dylib'
        sh('xcrun', 'clang', '-arch', 'x86_64', '-dynamiclib', '-install_name', f'{FRAMEWORK}/CoreWiFi',
           cls.tmp/'b.c', '-o', cls.plain)

    def system(self):
        root = Path(tempfile.mkdtemp(dir=self.tmp))
        resources = root/FRAMEWORK[1:]/'Resources'
        resources.mkdir(parents=True)
        (resources/'Info.plist').write_text('<plist/>')
        airportd = root/'usr/libexec/airportd'
        airportd.parent.mkdir(parents=True)
        airportd.write_bytes(b'\xcf\xfa\xed\xfe tahoe airportd')
        return root

    def test_installs_exactly_two_files(self):
        root = self.system()
        before = tree(root)
        INSTALL['install'](root, self.tahoe, self.compat)
        after = tree(root)
        added = {k: v for k, v in after.items() if k not in before}
        self.assertEqual(sorted(added), [FRAMEWORK[1:] + '/CoreWiFi', FRAMEWORK[1:] + '/CoreWiFiTahoe.dylib'])
        self.assertEqual({k: after[k] for k in before}, before)
        self.assertEqual(added[FRAMEWORK[1:] + '/CoreWiFi'], hashlib.sha256(self.compat.read_bytes()).hexdigest())
        self.assertEqual((root/FRAMEWORK[1:]/'CoreWiFi').stat().st_mode & 0o777, 0o755)
        INSTALL['install'](root, self.tahoe, self.compat)   # same bytes again: accepted
        self.assertEqual(tree(root), after)

    def test_refuses_other_corewifi(self):
        root = self.system()
        (root/FRAMEWORK[1:]/'CoreWiFi').write_bytes(b'something else')
        with self.assertRaises(SystemExit):
            INSTALL['install'](root, self.tahoe, self.compat)

    def test_refuses_missing_framework(self):
        root = Path(tempfile.mkdtemp(dir=self.tmp))
        with self.assertRaises(SystemExit):
            INSTALL['install'](root, self.tahoe, self.compat)

    def test_checks_names_and_reexport(self):
        root = self.system()
        with self.assertRaises(SystemExit):
            INSTALL['install'](root, self.compat, self.compat)
        with self.assertRaises(SystemExit):
            INSTALL['install'](root, self.tahoe, self.plain)
        self.assertEqual(INSTALL['dylib_names'](self.compat), (f'{FRAMEWORK}/CoreWiFi', [f'{FRAMEWORK}/CoreWiFiTahoe.dylib']))

    def test_hook_passes_the_patch_inputs(self):
        hook = (PATCH/'src/kc-hook.sh').read_text()
        inputs = json.loads((PATCH/'patch.json').read_text())['inputs']
        self.assertEqual(inputs, ['corewifi_tahoe', 'corewifi_compat'])
        self.assertIn('"$H/inputs/corewifi_tahoe" "$H/inputs/corewifi_compat"', hook)
        self.assertNotIn('airportd', hook.split('\n', 6)[-1])


if __name__ == '__main__':
    unittest.main()
