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


class FakeHost:
    def __init__(self, uid=0, csr=0x803, args='amfi_get_out_of_my_way=1'):
        self.uid, self.csr, self.args = uid, csr, args

    def euid(self):
        return self.uid

    def csr_config(self):
        return self.csr

    def run(self, argv, check=True, timeout=120):
        import subprocess
        return subprocess.CompletedProcess(argv, 0, ('boot-args\t%s\n' % self.args).encode(), b'')


class BuildImageHere(unittest.TestCase):
    def test_stages_assembles_into_an_image_file_and_collects(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / 'Desktop' / 'macOS.img'
            ran = []

            def run(cmd):
                cmd = [str(x) for x in cmd]; ran.append(cmd)
                if '--stage-kit' in cmd:
                    (Path(cmd[cmd.index('--work') + 1]) / 'share').mkdir(parents=True)
                if cmd[0] == 'sh':
                    local = Path(cmd[3]); local.mkdir(parents=True); (local / 'target.img').write_bytes(b'img')
                if cmd[0] == 'mv':
                    os.replace(cmd[1], cmd[2])
                return 0
            c, out_text = console()
            a = mock.Mock(out=str(out), work=d + '/work')
            path = os.environ.get('PATH')
            try:
                self.assertEqual(0, BUILD.build_image_here(a, c, FakeHost(), Path('a.json'), {}, run=run))
            finally:
                os.environ['PATH'] = path
            stage, assemble, collect, move = ran
            self.assertIn('--stage-kit', stage)
            self.assertEqual(['sh'], assemble[:1]); self.assertEqual(4, len(assemble))   # image target: no --disk
            self.assertIn('--collect-kit', collect)
            self.assertEqual(b'img', out.read_bytes())
            self.assertIn('image built', out_text.getvalue())

    def test_refuses_an_everyday_mac_and_an_existing_file(self):
        with tempfile.TemporaryDirectory() as d:
            ran = []
            for host, why in ((FakeHost(uid=501), 'root'), (FakeHost(csr=0), 'filesystem protection'),
                              (FakeHost(args='-v'), 'AMFI')):
                c, _ = console()
                with self.assertRaises(CON.Stopped) as x:
                    BUILD.build_image_here(mock.Mock(out=d + '/new.img', work=None), c, host, Path('a.json'), {},
                                           run=ran.append)
                self.assertIn(why, x.exception.what)
            (Path(d) / 'old.img').write_bytes(b'')
            with self.assertRaises(CON.Stopped):
                BUILD.build_image_here(mock.Mock(out=d + '/old.img', work=None), console()[0], FakeHost(),
                                       Path('a.json'), {}, run=ran.append)
            self.assertEqual([], ran)


class OclpArchive(unittest.TestCase):
    def test_check_here_wants_the_oclp_source_archive_not_the_vm(self):
        with tempfile.TemporaryDirectory() as d:
            store = Path(d)
            asm = store / 'a.json'; asm.write_text('{"oclp": "abc123"}')
            with mock.patch.object(ASSETS, 'rows', lambda *_: []):
                self.assertEqual(['builder: missing oclp-source/abc123.tar (and .commit)'],
                                 ASSETS.check(asm, store, here=True))
                (store / 'oclp-source').mkdir()
                (store / 'oclp-source/abc123.tar').write_bytes(b'')
                (store / 'oclp-source/abc123.commit').write_text('abc123full\n')
                self.assertEqual([], ASSETS.check(asm, store, here=True))
                self.assertEqual(len(ASSETS.TOOLS), len(ASSETS.check(asm, store)))   # the VM build wants the VM


if __name__ == '__main__':
    unittest.main()
