"""toolchain/make: dyld's VersionMap.h is generated only from AvailabilityVersions-157.2's own files."""
from importlib.machinery import SourceFileLoader
import importlib.util
from pathlib import Path
import tempfile
import unittest

PATH = Path(__file__).resolve().parents[1] / 'toolchain/make'
LOADER = SourceFileLoader('toolchain_make', str(PATH))
SPEC = importlib.util.spec_from_loader('toolchain_make', LOADER)
MAKE = importlib.util.module_from_spec(SPEC)
LOADER.exec_module(MAKE)


class VersionMap(unittest.TestCase):
    def test_pins_name_the_three_files_used(self):
        self.assertEqual({'availability', 'availability.dsl', 'templates/VersionMap.h'}, set(MAKE.AVAILABILITY_FILES))
        self.assertTrue(all(len(h) == 64 for h in MAKE.AVAILABILITY_FILES.values()))

    def test_a_different_copy_is_refused_before_anything_runs(self):
        with tempfile.TemporaryDirectory() as scratch:
            source = Path(scratch) / 'availability-versions'
            (source / 'templates').mkdir(parents=True)
            for name in MAKE.AVAILABILITY_FILES:
                (source / name).write_text('not the pinned file\n')
            destination = Path(scratch) / 'tree/include/dyld/VersionMap.h'
            with self.assertRaisesRegex(SystemExit, 'sha256 differs'):
                MAKE.version_map(source, Path(scratch), destination)
            self.assertFalse(destination.exists())


class SourcePatch(unittest.TestCase):
    """A DYLD_SOURCE that already carries source.patch must stop the build, not be silently un-patched."""
    PATCH = ('--- a/f.h\n+++ b/f.h\n@@ -1,2 +1,3 @@\n a\n+ours\n b\n')

    def apply(self, text):
        import subprocess
        with tempfile.TemporaryDirectory() as scratch:
            (Path(scratch) / 'f.h').write_text(text)
            (Path(scratch) / 'p').write_text(self.PATCH)
            run = subprocess.run(['patch', '-p1', '--forward', '--batch', '-i', 'p'], cwd=scratch,
                                 capture_output=True, text=True)
            return run.returncode, (Path(scratch) / 'f.h').read_text()

    def test_make_uses_forward_batch(self):
        self.assertIn("'--forward', '--batch'", PATH.read_text())

    def test_pristine_source_is_patched(self):
        self.assertEqual(self.apply('a\nb\n'), (0, 'a\nours\nb\n'))

    def test_patched_source_is_refused_unchanged(self):
        rc, text = self.apply('a\nours\nb\n')
        self.assertNotEqual(rc, 0)
        self.assertEqual(text, 'a\nours\nb\n')


if __name__ == '__main__':
    unittest.main()
