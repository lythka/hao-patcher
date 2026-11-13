"""hao assets (make, check) and build-image --assets / --export; fixtures only, the real rows' enumeration is
replaced, nothing reads the store."""
import contextlib
import io
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'src'))
import assets as ASSETS   # noqa: E402
import builder as BUILD   # noqa: E402
import console as CON     # noqa: E402


def fake_rows(*_):
    return [('a_file', Path('/lock'), 'f' * 64, 'store/artifacts/a/a.dylib'),
            ('a_folder', Path('/lock'), '-', 'store/artifacts/b'),
            ('tracked', Path('/lock'), 'e' * 64, 'patches/x/src/tool.py'),
            ('unnamed', None, None, None)]


class Assets(unittest.TestCase):
    def store(self, root):
        s = root / 'store'
        (s / 'artifacts/a').mkdir(parents=True); (s / 'artifacts/a/a.dylib').write_bytes(b'x')
        (s / 'artifacts/b').mkdir(); (s / 'artifacts/b/inside').write_text('y')
        for tool in ASSETS.TOOLS:
            (s / tool).mkdir(parents=True); (s / tool / 'marker').write_text(tool)
        return s

    def test_in_store(self):
        self.assertEqual(Path('artifacts/a'), ASSETS.in_store('store/artifacts/a'))
        self.assertIsNone(ASSETS.in_store('patches/x/src/tool.py'))
        self.assertIsNone(ASSETS.in_store('store'))

    def test_make_clones_rows_and_tools_below_store(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(ASSETS, 'rows', fake_rows):
            root = Path(d); store = self.store(root); dest = root / 'assets'
            made = ASSETS.make(dest, 'assembly.json', store)
            self.assertEqual(b'x', (dest / 'artifacts/a/a.dylib').read_bytes())
            self.assertEqual('y', (dest / 'artifacts/b/inside').read_text())
            for tool in ASSETS.TOOLS:
                self.assertTrue((dest / tool / 'marker').is_file(), tool)
            self.assertEqual({'a_file', 'a_folder', 'builder'}, {w for w, _ in made})
            with self.assertRaises(ValueError):
                ASSETS.make(dest, 'assembly.json', store)   # not empty

    def test_check_names_missing_rows_tools_and_wrong_hashes(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(ASSETS, 'rows', fake_rows):
            store = self.store(Path(d))
            (store / 'artifacts/b/inside').unlink(); (store / 'artifacts/b').rmdir()
            (store / 'vm/base/marker').unlink(); (store / 'vm/base').rmdir()

            def path(folder, name):
                raise SystemExit('%s: wrong sha256' % name)
            with mock.patch.object(ASSETS, '_lock', return_value={'path': path}):
                problems = ASSETS.check('assembly.json', store)
            self.assertIn('a_folder: missing (artifacts/b)', problems)
            self.assertIn('builder: missing vm/base', problems)
            self.assertIn('unnamed: no input row names it', problems)
            self.assertTrue(any(p.startswith('a_file: a_file: wrong sha256') for p in problems), problems)


def console():
    out = io.StringIO()
    return CON.Console('steps', out=out, ask=lambda q: ''), out


class BuildImageAssets(unittest.TestCase):
    def test_links_a_fresh_clones_store_and_checks_it(self):
        with tempfile.TemporaryDirectory() as d:
            top = Path(d) / 'clone'; top.mkdir(); folder = Path(d) / 'assets'; folder.mkdir()
            c, _ = console()
            a = mock.Mock(assets=str(folder))
            with mock.patch.object(BUILD, 'TOP', top), mock.patch.object(BUILD.ASSETS, 'check', return_value=[]):
                BUILD.use_assets(a, c, 'assembly.json')
            self.assertTrue((top / 'store').is_symlink())
            self.assertEqual(folder.resolve(), (top / 'store').resolve())

    def test_refuses_another_store_and_missing_inputs(self):
        with tempfile.TemporaryDirectory() as d:
            top = Path(d) / 'clone'; top.mkdir(); folder = Path(d) / 'assets'; folder.mkdir()
            other = Path(d) / 'other'; other.mkdir(); (top / 'store').symlink_to(other)
            c, _ = console()
            with mock.patch.object(BUILD, 'TOP', top), self.assertRaises(CON.Stopped) as x:
                BUILD.use_assets(mock.Mock(assets=str(folder)), c, 'assembly.json')
            self.assertIn('already has a store', x.exception.what)
            (top / 'store').unlink()
            with mock.patch.object(BUILD, 'TOP', top), \
                    mock.patch.object(BUILD.ASSETS, 'check', return_value=['kdk: missing (inputs/kdk)']), \
                    self.assertRaises(CON.Stopped) as x:
                BUILD.use_assets(mock.Mock(assets=str(folder)), c, 'assembly.json')
            self.assertIn('1 inputs are missing', x.exception.what)

    def test_export_runs_ssdimg_export(self):
        c, _ = console()
        ran = []
        BUILD.export_image(mock.Mock(export='/tmp/x.img'), c, Path('/repo'), run=lambda cmd: ran.append(cmd) or 0)
        self.assertEqual(['-C', '/repo', 'export', str(Path('/tmp/x.img').resolve())], ran[0][1:])
        self.assertTrue(ran[0][0].endswith('tools/bin/ssdimg'))


if __name__ == '__main__':
    unittest.main()
