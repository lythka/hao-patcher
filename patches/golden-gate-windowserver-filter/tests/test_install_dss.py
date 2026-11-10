"""Test the package-owned display installer callback contract without an assembler."""
import importlib.util
from pathlib import Path
import shutil
import tempfile
import unittest

PATCH = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('install_dss', PATCH/'src/install_dss.py')
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)


class DisplayInstall(unittest.TestCase):
    def test_owned_copy_and_launcher_dependency_contract(self):
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch)
            source = root/'input.dylib'
            source.write_bytes(b'display fixture')
            operations = []
            def copy(source, target):
                operations.append(('copy', str(target.relative_to(root))))
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
            def resign(kept, golden, workspace, dylibs):
                operations.append(('resign', str(kept.relative_to(root)), str(golden.relative_to(root)), dylibs))
                return 'fixture.identifier'
            helper.install_library(root/'system', source, copy)
            helper.resign_clients(root/'system', root/'golden', root/'workspace',
                                  (helper.DISPLAY_STATE_CLIENTS[0], 'Other/Launcher'), resign)
            self.assertEqual((root/'system/usr/lib/dss_shim.dylib').read_bytes(), b'display fixture')
            self.assertEqual(operations[0], ('copy', 'system/usr/lib/dss_shim.dylib'))
            self.assertEqual(operations[1][3], ('usr/lib/dss_shim.dylib',))
            self.assertEqual(operations[2][3], ())
            self.assertEqual(operations[1][1], 'system/'+helper.DISPLAY_STATE_CLIENTS[0])
            self.assertEqual(operations[1][2], 'golden/'+helper.DISPLAY_STATE_CLIENTS[0])


if __name__ == '__main__':
    unittest.main()
