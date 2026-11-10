"""Build and prepare a complete display payload using authored launcher fixtures."""
import json
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'lib'))
import package


class StandaloneTest(unittest.TestCase):
    def launcher(self, root, name):
        source, metadata, entitlements = (root / (name + suffix) for suffix in ('.c', '.plist', '.entitlements'))
        source.write_text('int main(void) { return 0; }\n')
        metadata.write_bytes(plistlib.dumps(dict(CFBundleIdentifier='com.apple.WindowServer.fixture',
                                               Fixture=name)) + b' ' * 2048)
        entitlements.write_bytes(plistlib.dumps({'com.apple.security.get-task-allow': True}))
        binary = root / name
        package.run('xcrun', 'clang', '-arch', 'x86_64', '-mmacosx-version-min=26.0',
                    source, '-Wl,-headerpad,4096', '-Wl,-sectcreate,__TEXT,__info_plist,' + str(metadata), '-o', binary)
        package.run('codesign', '-f', '-s', '-', '-i', 'com.apple.WindowServer.fixture',
                    '--entitlements', entitlements, binary)
        return binary

    def test_complete_build_prepare_and_offline_install_contract(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            artifact = package.build(root / 'artifact')
            self.assertEqual({path.name for path in artifact.glob('*.dylib')},
                             {'dss_shim.dylib', 'windowserver_filter.dylib'})
            record = json.loads((artifact / 'build.json').read_text())
            self.assertNotIn('build.json', record['outputs'])
            for name, digest in record['outputs'].items():
                self.assertEqual(package.sha(artifact / name), digest)
            native, golden = self.launcher(root, 'Native'), self.launcher(root, 'Golden')
            original = native.read_bytes(), golden.read_bytes()
            prepared = package.prepare(artifact, native, golden, root / 'prepared')
            self.assertEqual((native.read_bytes(), golden.read_bytes()), original)
            payload = prepared / 'payload'
            self.assertIn('/usr/lib/dss_shim.dylib', package.run('otool', '-L', payload / package.WINDOWSERVER))
            self.assertIn('/usr/lib/windowserver_filter.dylib', package.run('otool', '-L', payload / 'usr/lib/dss_shim.dylib'))
            for relative in (package.WINDOWSERVER, *package.LIBRARIES):
                package.run('codesign', '--verify', '--strict', payload / relative)
            # Installation is exercised on a regular directory with ownership/flags mocked.
            system = root / 'offline-system'
            system.mkdir()
            with patch.object(package.os, 'geteuid', return_value=0), patch.object(package.os, 'chown'), \
                 patch.object(package.os, 'chflags'):
                package.install(prepared, system)
            for relative in (package.WINDOWSERVER, *package.LIBRARIES):
                self.assertEqual((system / relative).read_bytes(), (payload / relative).read_bytes())
            with self.assertRaises(FileExistsError):
                package.prepare(artifact, native, golden, prepared)

    def test_live_root_is_refused_before_copy(self):
        with patch.object(package.shutil, 'copyfile') as copy:
            with self.assertRaisesRegex(ValueError, 'offline System'):
                package.install(Path('/unused'), Path('/'))
            copy.assert_not_called()


if __name__ == '__main__':
    unittest.main()
