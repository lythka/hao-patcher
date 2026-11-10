"""Exercise captured converter behavior and the component artifact recipe."""
import importlib.machinery
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

HERE = Path(__file__).resolve().parents[1]
loader = importlib.machinery.SourceFileLoader('display_state_shim', str(HERE / 'display-state-shim'))
spec = importlib.util.spec_from_loader(loader.name, loader)
module = importlib.util.module_from_spec(spec)
loader.exec_module(module)


class PackageTests(unittest.TestCase):
    def test_converter_and_signed_package(self):
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch)
            converter = root / 'test_convert'
            subprocess.run(['xcrun', 'clang', '-arch', 'x86_64',
                            '-mmacosx-version-min=26.0', '-O1', '-Wall',
                            '-o', str(converter), 'tests/test_convert.c', 'src/dss_convert.c'],
                           cwd=HERE, check=True)
            subprocess.run(['codesign', '-s', '-', '-f', str(converter)], check=True)
            result = subprocess.check_output([str(converter)], text=True)
            self.assertIn('PASS: 0 failures', result)
            self.assertEqual(result.count('ok   '), 14)
            artifact = root / 'artifact'
            module.build(artifact)
            record = json.loads((artifact / 'build.json').read_text())
            dylib = artifact / 'dss_shim.dylib'
            self.assertEqual(record['outputs']['dss_shim.dylib'], module.digest(dylib))
            subprocess.run(['codesign', '--verify', str(dylib)], check=True)
            self.assertEqual(subprocess.check_output(['lipo', '-archs', str(dylib)],
                                                    text=True).strip(), 'x86_64')
            self.assertTrue(all(Path(item['path']).is_file() for item in record['inputs']))
            loader = root / 'test_load'
            subprocess.run(['xcrun', 'clang', '-arch', 'x86_64',
                            '-mmacosx-version-min=26.0', '-o', str(loader),
                            'tests/test_load.c'], cwd=HERE, check=True)
            subprocess.run(['codesign', '-s', '-', '-f', str(loader)], check=True)
            self.assertIn('PASS: dlopen ok', subprocess.check_output(
                [str(loader), str(dylib)], text=True))
            # An existing destination is a distinct artifact, never updated in place.
            with self.assertRaises(FileExistsError):
                module.build(artifact)


if __name__ == '__main__':
    unittest.main()
