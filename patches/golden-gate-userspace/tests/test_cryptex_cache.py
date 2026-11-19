"""The cryptex cache (src/cryptex_cache.py): what its key covers, the sparse copy, and store/restore.

Always: the key's source files are exactly the code cryptex.py runs (its local imports, found by importing it) plus
build/, and leave out install.py's other work; any covered input changes the key; a stored image comes back byte for
byte with the same sha256; a damaged or mismatched entry is not used.
With CRYPTEX_CACHE_IMAGE=PATH (a real sealed os.dmg, e.g. from an exported image's Preboot, read only): the
copy of it is byte-identical, has the file's own sha256 and is fully allocated (no holes: #90).
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
        self.assertEqual(sorted(f for f in files if '/' not in f and f not in cc.DATA_FILES), sorted(loaded))
        self.assertEqual([f for f in cc.DATA_FILES if f in files], list(cc.DATA_FILES))   # the data the code reads
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


class TargetLines(unittest.TestCase):
    def lines(self, data, model='M'):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)/'target-data.json'
            path.write_text(json.dumps(data))
            saved = {k: os.environ.get(k) for k in ('HAO_TARGET_MODEL', 'HAO_TARGET_DATA')}
            os.environ.update(HAO_TARGET_MODEL=model, HAO_TARGET_DATA=str(path))
            try:
                return cc.target_lines()
            finally:
                for k, v in saved.items():
                    os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)

    def test_only_the_model_and_its_model_gates_count(self):
        """The model's OpenCore reasons in platform_data do not change the images, so they do not rebuild them."""
        gates = {'model_gates': {'renamed': 'X'}}
        base = self.lines(gates)
        self.assertEqual(self.lines(dict(gates, opencore_reasons={'kext:A.kext': 'r'})), base)
        self.assertNotEqual(self.lines({'model_gates': {'renamed': 'Y'}}), base)
        self.assertNotEqual(self.lines(gates, model='N'), base)
        self.assertEqual(self.lines({'opencore_generic': True})[1], 'target model_gates none')


class Copy(unittest.TestCase):
    def holey(self, path):
        """Data, a real hole (APFS leaves small gaps allocated), written zeros aligned and unaligned to the copy's
        1 MiB pieces, another real hole, data, a trailing hole; skips where the file system reports no holes."""
        mib = 1 << 20
        sparse(path, [(0, os.urandom(mib)), (64 * mib, b'\0' * (4 * mib)), (69 * mib + 4096, b'\0' * (mib + 8192)),
                      (128 * mib, os.urandom(mib + 5)), (200 * mib, b'')])
        with open(path, 'r+b') as stream:
            stream.truncate(260 * mib)
        if not cc.hole_map(path):
            self.skipTest('this file system reports no holes (SEEK_HOLE)')
        return cc.hole_map(path)

    def test_copy_is_byte_identical_and_fully_allocated(self):
        """#90: the boot's APFS graft cannot map a hole of os.dmg (ENOENT, then SIGBUS in the x86_64 shared cache),
        whether the copy made it or the source had it: the copy has none."""
        with tempfile.TemporaryDirectory() as tmp:
            source, target = Path(tmp)/'source.img', Path(tmp)/'target.img'
            holes = self.holey(source)
            written = ((64 << 20, 68 << 20), ((69 << 20) + 4096, (70 << 20) + 12288))      # the zeros: data, not holes
            self.assertFalse([h for h in holes for w in written if h[0] < w[1] and h[1] > w[0]], holes)
            got = cc.copy_file(source, target)
            self.assertEqual(got, dict(sha256=sha(source), size=source.stat().st_size))
            self.assertEqual(target.read_bytes(), source.read_bytes())
            self.assertEqual(cc.hole_map(target), [])
            self.assertGreaterEqual(target.stat().st_blocks * 512, source.stat().st_size)
            with self.assertRaises(FileExistsError):
                cc.copy_file(source, target)

    def test_allocate_fills_every_hole_and_keeps_the_bytes(self):
        """install_cryptex allocates the built (sparse: new_image truncates) or reused images before they go to Preboot."""
        with tempfile.TemporaryDirectory() as tmp:
            image = Path(tmp)/'os.dmg'
            holes = self.holey(image)
            before = sha(image)
            self.assertEqual(cc.allocate(image), sum(e - s for s, e in holes))
            self.assertEqual(cc.hole_map(image), [])
            self.assertEqual(sha(image), before)
            self.assertEqual(cc.allocate(image), 0)

    def test_install_allocates_both_images_built_or_reused(self):
        text = (SRC/'install.py').read_text()
        body = text[text.index('def install_cryptex'):text.index('def arguments')]
        self.assertIn("cryptex_cache.allocate(path)", body)
        self.assertIn("(('os.dmg', os_work), ('app.dmg', app_work))", body)
        self.assertLess(body.index('cryptex_cache.allocate'), body.index("os.replace(os_work"))   # before Preboot

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
            self.assertEqual(cc.hole_map(target), [])                      # fully allocated (#90)


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
