"""The package works without any assembler or other-OS source checkout."""
import importlib.util
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parents[1]  # repository root: patches/, cache/, tools/
class StandaloneTests(unittest.TestCase):
    def test_command_help_has_local_operations(self):
        text = subprocess.check_output([sys.executable, '-I', str(ROOT / 'metal-fixes')], text=True)
        self.assertIn('metallibs', text)
        self.assertIn('framebuffer', text)
        self.assertIn('install', text)

    def test_mps_module_imports_local_cache_reader_and_exact_fix(self):
        for path in (REPO / 'patches/golden-gate-userspace/src', ROOT / 'lib'):
            sys.path.insert(0, str(path))
            self.addCleanup(sys.path.remove, str(path))
        import mps_matrix_initializer as mps
        result = mps.patch_initializer(mps.ORIGINAL)
        self.assertTrue(result.changed)
        self.assertEqual(len(result.data), len(mps.ORIGINAL))
        self.assertEqual(sum(a != b for a, b in zip(result.data, mps.ORIGINAL)), 2)

    def test_framebuffer_optional_runtime_test_imports_without_parent_source(self):
        file = REPO / 'patches/golden-gate-metal-framebuffer/build/tests/test_frontend.py'
        spec = importlib.util.spec_from_file_location('framebuffer_frontend_test', file)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)


if __name__ == '__main__':
    unittest.main()
