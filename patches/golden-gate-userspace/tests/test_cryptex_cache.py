"""The cryptex cache (src/cryptex_cache.py): what its key covers, the sparse copy, and store/restore.

Always: the key's source files are exactly the code cryptex.py runs (its local imports, found by importing it) plus
build/, and leave out install.py's other work; any covered input changes the key; a stored image comes back byte for
byte with the same sha256; a damaged or mismatched entry is not used.
With CRYPTEX_CACHE_IMAGE=PATH (a real sealed os.dmg, e.g. from an exported image's Preboot, read only): the sparse
copy of it is byte-identical, has the file's own sha256 and keeps its holes.
python3.14 -I -B -m unittest discover -s patches/golden-gate-userspace/tests -k cryptex_cache"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

SRC = Path(__file__).resolve().parents[1]/'src'
sys.path.insert(0, str(SRC))
import cryptex_cache as cc   # noqa: E402

GUEST = ['builder macOS TEST', 'builder python 3.12.0', 'builder newfs_apfs 0', 'builder apfs_prepare_cryptex 0']


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def sparse(path, layout):
    """A file of (offset, bytes) pieces, holes elsewhere, of the size the last piece ends at (or layout's 'size')."""
    with open(path, 'wb') as stream:
        size = 0
        for offset, data in layout:
            stream.seek(offset)
            stream.write(data)
            size = max(size, offset + len(data))
        stream.truncate(size)


class SourceFiles(unittest.TestCase):
    def test_source_files_are_what_cryptex_imports_plus_build(self):
        files = cc.source_files(SRC)
        # what importing cryptex.py actually loads from this folder
        code = ('import sys; sys.path.insert(0, sys.argv[1]); import cryptex, cryptex_cache, os; '
                'print("\\n".join(sorted(os.path.basename(m.__file__) for m in list(sys.modules.values()) '
                'if getattr(m, "__file__", None) and os.path.dirname(os.path.abspath(m.__file__)) == sys.argv[1])))')
        loaded = subprocess.run([sys.executable, '-I', '-B', '-c', code, str(SRC)], check=True, capture_output=True,
                                text=True).stdout.split()
        self.assertEqual(sorted(f for f in files if '/' not in f), sorted(loaded))
        self.assertNotIn('install.py', files)
        for name in ('cryptex.py', 'cryptex_cache.py', 'cache_pages.py', 'swift_refs.py', 'codeid_carrier.py',
                     'cryptex_stubs.py', 'legacy_trackpad.py', 'macho.py'):
            self.assertIn(name, files)

    def test_build_folder_counts_and_caches_do_not(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)/'recipe'
            shutil.copytree(SRC, folder, ignore=shutil.ignore_patterns('__pycache__'))
            (folder/'build/metal-fixes/__pycache__').mkdir(parents=True)
            (folder/'build/metal-fixes/metal-fixes').write_text('one')
            (folder/'build/metal-fixes/__pycache__/x.pyc').write_text('cache')
            files = cc.source_files(folder)
            self.assertIn('build/metal-fixes/metal-fixes', files)
            self.assertFalse(any('__pycache__' in f for f in files))

    def test_install_py_builds_no_image_itself(self):
        """install.py's image work all goes through cryptex.py (build_os_image, build_app_image), so the key needs
        no part of install.py."""
        text = (SRC/'install.py').read_text()
        for name in ('def new_image', 'def seal_image', 'def merge_rosetta', 'apfs_prepare_cryptex', 'newfs_apfs',
                     'resign_file', 'plan_cache_family', 'cryptex_stubs.embed'):
            self.assertNotIn(name, text)
        self.assertIn('build_os_image(', text)
        self.assertIn('build_app_image(', text)


class Key(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.folder = root/'recipe'
        shutil.copytree(SRC, self.folder, ignore=shutil.ignore_patterns('__pycache__'))
        self.rows = {}
        for name in cc.ROWS:
            (root/name).mkdir()
            (root/name/'build.json').write_text('{}')
            self.rows[name] = (f'row {name} store/artifacts/{name}', root/name)
        (root/'driverkit').mkdir()
        self.driverkit = ('row tahoe_driverkit_donors store/artifacts/dk', root/'driverkit')
        (root/'tahoe-donors').mkdir()
        (root/'tahoe-donors/make').write_text('tool')
        self.tool = root/'tahoe-donors'

    def tearDown(self):
        self.tmp.cleanup()

    def key(self):
        return cc.key_of(cc.host_lines(self.folder, self.rows, self.driverkit, self.tool) + GUEST)

    def test_every_covered_input_changes_the_key_and_install_py_does_not(self):
        base = self.key()
        self.assertEqual(self.key(), base)
        (self.folder/'install.py').write_text('# install.py changed\n')
        self.assertEqual(self.key(), base)
        for change in (lambda: (self.folder/'swift_refs.py').write_text('# changed\n'),
                       lambda: (self.folder/'cryptex.py').write_text('# changed\n'),
                       lambda: self.rows.update(hao_runtime=('row hao_runtime store/artifacts/other',
                                                             self.rows['hao_runtime'][1])),
                       lambda: (self.rows['codeid_carrier'][1]/'build.json').write_text('{"other": 1}'),
                       lambda: (self.tool/'make').write_text('tool 2'),
                       lambda: (self.driverkit[1]/'build.json').write_text('{}')):
            change()
            self.assertNotEqual(self.key(), base)
            base = self.key()
        self.assertNotEqual(cc.key_of(cc.host_lines(self.folder, self.rows, self.driverkit, self.tool) + GUEST[:-1]
                                      + ['builder apfs_prepare_cryptex 1']), base)


class Copy(unittest.TestCase):
    def test_sparse_copy_is_byte_identical_and_keeps_holes(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, target = Path(tmp)/'source.img', Path(tmp)/'target.img'
            mib = 1 << 20
            sparse(source, [(0, b'head' * 1000), (64 * mib + 5, os.urandom(3 * mib + 7)), (200 * mib, b'\0' * mib),
                            (300 * mib - 3, b'end')])
            got = cc.copy_file(source, target)
            self.assertEqual(got, dict(sha256=sha(source), size=source.stat().st_size))
            self.assertEqual(target.read_bytes(), source.read_bytes())
            self.assertLess(target.stat().st_blocks * 512, 16 * mib)
            with self.assertRaises(FileExistsError):
                cc.copy_file(source, target)

    def test_empty_and_unaligned_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            for n, data in enumerate((b'', b'x', os.urandom(cc.CHUNK + 1))):
                source, target = Path(tmp)/f's{n}', Path(tmp)/f't{n}'
                source.write_bytes(data)
                self.assertEqual(cc.copy_file(source, target)['sha256'], hashlib.sha256(data).hexdigest())
                self.assertEqual(target.read_bytes(), data)

    @unittest.skipUnless(os.environ.get('CRYPTEX_CACHE_IMAGE'), 'set CRYPTEX_CACHE_IMAGE to a real sealed os.dmg')
    def test_real_image(self):
        image = Path(os.environ['CRYPTEX_CACHE_IMAGE'])
        with tempfile.TemporaryDirectory(dir=os.environ.get('CRYPTEX_CACHE_SCRATCH')) as tmp:
            target = Path(tmp)/'os.dmg'
            got = cc.copy_file(image, target)
            self.assertEqual(got['sha256'], cc.sha256(image))
            self.assertEqual(cc.sha256(target), got['sha256'])
            self.assertEqual(target.stat().st_size, image.stat().st_size)
            subprocess.run(['cmp', str(image), str(target)], check=True)
            # holes kept: within 1% of the source's allocation (1 MiB copy pieces vs. the source's own extents)
            self.assertLessEqual(target.stat().st_blocks, image.stat().st_blocks * 101 // 100)


class StoreRestore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.share = root/'cryptex-cache'
        (self.share/'in').mkdir(parents=True)
        (self.share/'key.json').write_text(json.dumps(dict(host_key='h', host_lines=['schema x', 'row a 1'])))
        self.current = root/'current'
        self.current.mkdir()
        self.payload = root/'payload'
        self.payload.mkdir()
        (self.payload/'app.dmg').write_bytes(b'apple app')
        sparse(root/'os.built', [(0, b'os image'), (8 << 20, b'tail')])
        (root/'app.built').write_bytes(b'stubbed app')
        self.counts = {'seal': 'root hash x', 'resigned_4k': ['a']}
        self.app = dict(image='Fresh raw APFS container; AppOS files with hao stubs; sealed', seal='s')
        self.root = root

    def tearDown(self):
        self.tmp.cleanup()

    def stored(self, app=None):
        cache = cc.Cache(self.share, guest=GUEST)
        cache.store(self.root/'os.built', self.root/'app.built', self.counts, app or self.app)
        self.assertEqual(json.loads((self.share/'result.json').read_text())['outcome'], 'stored')
        os.rename(self.share/'out'/cache.key, self.share/'in'/cache.key)    # what golden-run does for the next build
        return cache

    def test_round_trip_is_byte_identical(self):
        cache = self.stored()
        os_work, app_work = self.current/'.os', self.current/'.app'
        counts, app = cc.Cache(self.share, guest=GUEST).restore(os_work, app_work, self.payload)
        self.assertEqual((counts, app), (self.counts, self.app))
        self.assertEqual(os_work.read_bytes(), (self.root/'os.built').read_bytes())
        self.assertEqual(app_work.read_bytes(), b'stubbed app')
        result = json.loads((self.share/'result.json').read_text())
        self.assertEqual((result['outcome'], result['key']), ('hit', cache.key))
        self.assertEqual(result['files']['os.dmg']['sha256'], sha(self.root/'os.built'))

    def test_apple_app_image_comes_from_the_payload(self):
        self.stored(app=dict(image="Apple's app.dmg", hao_stubs=dict(embedded=0, already=0)))
        cache = cc.Cache(self.share, guest=GUEST)
        self.assertFalse((self.share/'in'/cache.key/'app.dmg').exists())
        cache.restore(self.current/'.os', self.current/'.app', self.payload)
        self.assertEqual((self.current/'.app').read_bytes(), b'apple app')

    def test_other_tools_miss(self):
        self.stored()
        self.assertIsNone(cc.Cache(self.share, guest=GUEST[:-1] + ['builder apfs_prepare_cryptex 1'])
                          .restore(self.current/'.os', self.current/'.app', self.payload))
        self.assertEqual(json.loads((self.share/'result.json').read_text())['outcome'], 'miss')
        self.assertEqual(list(self.current.iterdir()), [])

    def test_damaged_entry_is_not_used_and_leaves_nothing(self):
        cache = self.stored()
        with open(self.share/'in'/cache.key/'app.dmg', 'r+b') as stream:
            stream.write(b'X')
        self.assertIsNone(cc.Cache(self.share, guest=GUEST).restore(self.current/'.os', self.current/'.app',
                                                                     self.payload))
        result = json.loads((self.share/'result.json').read_text())
        self.assertEqual((result['outcome'], result['file']), ('corrupt', 'app.dmg'))
        self.assertEqual(list(self.current.iterdir()), [])


if __name__ == '__main__':
    unittest.main()
