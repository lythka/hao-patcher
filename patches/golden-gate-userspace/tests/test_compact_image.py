"""The OS cryptex image is sized to what it holds (src/cryptex.py compact_image, fitted_size), not to its files' logical
size: install.py allocates the whole image (#90), so free space inside it costs Preboot space. No APFS calls here
(image creation is the builder VM's); the image steps are recorded fakes.
python3.14 -I -B -m unittest discover -s patches/golden-gate-userspace/tests -k compact"""
import contextlib
import os
from pathlib import Path
import sys
import tempfile
import unittest

SRC = Path(__file__).resolve().parents[1]/'src'
sys.path.insert(0, str(SRC))
import cryptex   # noqa: E402

GB = 10 ** 9


class FittedSize(unittest.TestCase):
    def test_26B131_numbers(self):
        """Measured on the 26B131 build (cache a9dc57c6): the image was made at 26.18 GB (logical size + 2 GiB) and its
        volume uses 13.25 GB; the fitted image is about 14.5 GB, so allocating it costs about 1 GB, not 13 GB."""
        used = 12937896 * 1024
        size = cryptex.fitted_size(used)
        self.assertGreater(size, used + (1 << 30) - 1)
        self.assertLess(size, 15 * GB)
        self.assertLess(size, 26180845568 * 6 // 10)
        self.assertEqual(size % (1 << 20), 0)

    def test_margin_grows_with_the_volume(self):
        self.assertEqual(cryptex.fitted_size(0), 1 << 30)
        self.assertGreaterEqual(cryptex.fitted_size(100 * GB) - 100 * GB, 3 * GB)


class Compact(unittest.TestCase):
    def test_copies_into_a_fitted_image_and_removes_the_first(self):
        calls = []
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            work, workspace = tmp/'os.dmg', tmp/'ws'
            workspace.mkdir()
            with open(work, 'wb') as f:
                f.truncate(26180845568)
            mounts = {}

            @contextlib.contextmanager
            def image_mount(image, folder, writable=False):
                if Path(image).suffix != '.dmg':        # hdiutil: "attach failed - image not recognized"
                    raise RuntimeError('hdiutil attach: image not recognized: %s' % Path(image).name)
                folder.mkdir()
                mounts[folder.name] = (Path(image).name, writable)
                calls.append(('mount', Path(image).name, writable))
                yield folder

            saved = {n: getattr(cryptex, n) for n in ('image_mount', 'new_image', 'replace_tree', 'used_bytes')}
            cryptex.image_mount = image_mount
            cryptex.new_image = lambda w, size, like: (calls.append(('new', Path(w).name, size)), open(w, 'wb').close())
            cryptex.replace_tree = lambda s, t, keep: calls.append(('copy', Path(s).name, Path(t).name, sorted(keep)))
            cryptex.used_bytes = lambda m: 13 * GB
            try:
                record = cryptex.compact_image(work, workspace, os.stat(tmp))
            finally:
                for n, f in saved.items():
                    setattr(cryptex, n, f)
            self.assertEqual(record, dict(built=26180845568, used=13 * GB, size=cryptex.fitted_size(13 * GB)))
            self.assertEqual(calls, [('mount', 'os.built.dmg', False),
                                     ('new', 'os.dmg', cryptex.fitted_size(13 * GB)),
                                     ('mount', 'os.dmg', True),
                                     ('copy', 'os.dmg-compact-source', 'os.dmg-compact-target', sorted(cryptex.CRYPTEX_KEEP))])
            self.assertTrue(work.is_file())
            self.assertFalse((tmp/'os.built.dmg').exists())
            self.assertEqual(cryptex.built_path(tmp/'.golden-gate-os.dmg').name, '.golden-gate-os.built.dmg')

    def test_install_removes_a_stale_first_image(self):
        text = (SRC/'install.py').read_text()
        body = text[text.index('def install_cryptex'):text.index('def arguments')]
        self.assertLess(body.index('remove(built_path(os_work))'), body.index('build_os_image('))

    def test_build_compacts_before_sealing(self):
        text = (SRC/'cryptex.py').read_text()
        body = text[text.index('def build_os_image'):text.index('def used_bytes')]
        self.assertLess(body.index('compact_image(work'), body.index('seal_image(work)'))


if __name__ == '__main__':
    unittest.main()
