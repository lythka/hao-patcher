"""cache/seeds: collect.sh on a fixture tree, make on a synthetic harvest (no Apple files, no laptop)."""
import io
import json
import os
import plistlib
import sqlite3
import struct
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
MAKE = HERE / 'make'
COLLECT = HERE / 'collect.sh'
SETUP_C = 'private/var/folders/zz/zyxvpxvq6csfxvn_n00000z000007r/C'
ROOT_C = 'private/var/folders/zz/zyxvpxvq6csfxvn_n0000000000000/C'
USER_C = 'private/var/folders/ll/43l1qwsx6k15l6djpy5zk00c0000gn/C'
STORE = 'Library/Caches/com.apple.iconservices.store'
SEED = 'Library/Application Support/PerUserSeed'


def index(records, size=172):
    return struct.pack('<5I', 11, 1, records, 4000, records * size) + b'\0' * (4000 * 8 + records * size)


def cache_info(build='26B131'):
    return plistlib.dumps({'language': 'en', 'ProductBuildVersion': build}, fmt=plistlib.FMT_BINARY)


def registry(path, home_fonts):
    db = sqlite3.connect(path)
    db.executescript('''
        CREATE TABLE version_table (a INTEGER, b INTEGER); INSERT INTO version_table VALUES (104, 88);
        CREATE TABLE font_table (f TEXT); INSERT INTO font_table VALUES ('Helvetica'), ('Times');
        CREATE TABLE bookmarks_table (b BLOB);
        CREATE TABLE dir_table (dir_column INTEGER PRIMARY KEY, path_column TEXT, domain_column INTEGER,
                                flags_column INTEGER, bookmark_ref_column BLOB);''')
    db.execute("INSERT INTO dir_table VALUES (1, '/System/Library/Fonts', 8, 0, NULL)")
    db.execute('INSERT INTO dir_table VALUES (3, ?, 1, 1536, ?)', (home_fonts, home_fonts.encode() + b'-bookmark'))
    db.commit()
    db.close()
    return Path(path).read_bytes()


class Fixture:
    """A harvest tar as collect.sh writes it."""

    def __init__(self, d, build='26B131', user_index_build='26B131', system_pcm_path=None):
        self.path = Path(d) / 'harvest.tar'
        self.folders = []
        members = []

        def add(name, data=None, uid=0, gid=0, mode=None):
            t = tarfile.TarInfo(name)
            t.uid, t.gid, t.mtime = uid, gid, 1794413707
            if data is None:
                t.type, t.mode = tarfile.DIRTYPE, mode or 0o755
            else:
                t.size, t.mode = len(data), mode or 0o644
            members.append((t, data))

        def tree(c, uid, bundle, files):
            self.folders += ['755 %d %d - %s/%s' % (uid, uid, c, '/'.join(bundle.split('/')[:i + 1]))
                             for i in range(len(bundle.split('/')))]
            add('%s/%s/com.apple.metal' % (c, bundle), uid=uid, gid=uid)
            for sub in ('0_0', '31001'):
                add('%s/%s/com.apple.metal/%s' % (c, bundle, sub), uid=uid, gid=uid)
            for f, data in files.items():
                add('%s/%s/com.apple.metal/%s' % (c, bundle, f), data, uid=uid, gid=uid)

        for c, uid in ((SETUP_C, 248), (ROOT_C, 0), (USER_C, 501)):
            parts = c.split('/')
            for i in range(4, len(parts) + 1):
                mode = 700 if i == len(parts) else 755
                self.folders.append('%d %d %d sunlnk %s' % (mode, uid if i > 4 else 0, uid, '/'.join(parts[:i])))
        tree(SETUP_C, 248, 'com.apple.SetupAssistant', {'0_0/functions.data': b'F' * 64, '0_0/functions.list': b'L'})
        tree(ROOT_C, 0, 'com.apple.Language-Chooser', {'31001/libraries.data': b'D' * 32})
        tree(USER_C, 501, 'com.apple.iconservicesagent/com.apple.iconservicesagent',
             {'0_0/functions.data': b'U' * 16, '0_0/functions.list': b'u'})
        add('%s/com.apple.controlcenter/com.apple.metalfe' % USER_C, uid=501, gid=20)
        self.folders.append('755 501 20 - %s/com.apple.controlcenter' % USER_C)
        # clang module caches (*.pcm) record their own folder: the user's (Preview) and Setup's
        fe = '%s/com.apple.Preview/com.apple.Preview/com.apple.metalfe/3BFB57ANS780' % USER_C
        add(fe.rsplit('/', 1)[0], uid=501, gid=20); add(fe, uid=501, gid=20)
        add(fe + '/monolithic_metal-4JF842TLW74.pcm', b'CPCH\0' + ('/' + fe.split('private/', 1)[1]).encode() + b'\0', 501, 20)
        self.folders += ['755 501 20 - %s/com.apple.Preview' % USER_C, '755 501 20 - %s/com.apple.Preview/com.apple.Preview' % USER_C]
        fe = '%s/com.apple.SetupAssistant/com.apple.metalfe/3BFB57ANS780' % SETUP_C
        add(fe.rsplit('/', 1)[0], uid=248, gid=248); add(fe, uid=248, gid=248)
        recorded = system_pcm_path or '/' + fe.split('private/', 1)[1]
        add(fe + '/monolithic_metal-4JF842TLW74.pcm', b'CPCH\0' + recorded.encode() + b'\0', 248, 248)
        # icons
        add(STORE, uid=240, gid=80, mode=0o711)
        add(STORE + '/store-info.plist', plistlib.dumps({'language': 'en', 'version': 4}), 240, 80)
        add(STORE + '/0A2D2B5C-0000-3000-8000-000000000001.isdata', b'\x09' + b'\0' * 47, 240, 80)
        self.folders.append('711 240 80 - ' + STORE)
        for c, uid, n, b in ((ROOT_C, 0, 3, build), (SETUP_C, 248, 5, build), (USER_C, 501, 7, user_index_build)):
            add(c + '/com.apple.iconservices', uid=uid, gid=uid, mode=0o711)
            add(c + '/com.apple.iconservices/store.index', index(n), uid, uid, 0o600)
            add(c + '/com.apple.iconservices/__cache_info__', cache_info(b), uid, uid)
        # fonts
        with tempfile.TemporaryDirectory() as t:
            setup = registry(Path(t) / 's', '/var/setup/Library/Fonts')
            user = registry(Path(t) / 'u', '/Users/benchtest/Library/Fonts')
        for c, uid, data in ((SETUP_C, 248, setup), (USER_C, 501, user)):
            add(c + '/com.apple.FontRegistry', uid=uid, gid=uid, mode=0o700)
            add(c + '/com.apple.FontRegistry/fontregistry.user', data, uid, uid)
        diag = {
            'diag/system.txt': 'ProductName:\t\tmacOS\nProductVersion:\t\t27.2.1\nBuildVersion:\t\t%s\n' % build,
            'diag/cache-dirs.tsv': '501\t%s\n0\t%s\n248\t%s\n' % (USER_C, ROOT_C, SETUP_C),
            'diag/folders.txt': '\n'.join(sorted(set(self.folders))) + '\n',
        }
        with tarfile.open(self.path, 'w') as t:
            for info, data in members:
                t.addfile(info, io.BytesIO(data) if data is not None else None)
            for name, text in diag.items():
                info = tarfile.TarInfo(name)
                info.size = len(text.encode())
                t.addfile(info, io.BytesIO(text.encode()))


def run(*args):
    return subprocess.run([sys.executable, '-I', '-B', str(MAKE), *map(str, args)], capture_output=True, text=True)


class MakeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def made(self, kind):
        found = sorted(self.d.glob('out/%s-26B131-*' % kind))
        self.assertEqual(len(found), 1, found)
        return found[0]

    def build(self, **kw):
        h = Fixture(self.d, **kw)
        (self.d / 'out').mkdir()
        return h, run(h.path, '--out', self.d / 'out')

    def test_artifacts(self):
        h, r = self.build()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('shader_caches_golden_gate', r.stdout)
        self.assertIn('icon_caches_golden_gate', r.stdout)
        self.assertIn('font_registry_golden_gate', r.stdout)
        # shaders: system trees in place with owners; user trees under PerUserSeed, root-owned; ancestors first
        with tarfile.open(self.made('shader-caches') / 'data.tar') as t:
            m = {x.name: x for x in t}
            names = [x.name for x in t]
        f = m[SETUP_C + '/com.apple.SetupAssistant/com.apple.metal/0_0/functions.data']
        self.assertEqual((f.uid, f.gid, f.size), (248, 248, 64))
        self.assertEqual(m[SETUP_C].mode, 0o700)
        self.assertEqual(m[SETUP_C + '/com.apple.SetupAssistant'].uid, 248)
        self.assertLess(names.index(SETUP_C), names.index(f.name))
        u = m[SEED + '/metal/com.apple.iconservicesagent/com.apple.iconservicesagent/com.apple.metal/0_0/functions.data']
        self.assertEqual((u.uid, u.gid, u.mode), (0, 0, 0o644))
        # the user's module caches stay out (path-bound); Setup's, recorded at their own path, ship
        self.assertFalse([n for n in names if n.startswith(SEED) and 'com.apple.metalfe' in n])
        self.assertIn(SETUP_C + '/com.apple.SetupAssistant/com.apple.metalfe/3BFB57ANS780/monolithic_metal-4JF842TLW74.pcm', m)
        self.assertEqual(json.loads((self.made('shader-caches') / 'build.json').read_text())
                         ['counts']['user_metalfe_skipped'], 1)
        self.assertFalse([n for n in names if n.startswith(USER_C)])
        self.assertFalse([n for n in names if 'iconservices/' in n or 'FontRegistry' in n])
        # icons
        a = self.made('icon-caches')
        with tarfile.open(a / 'icon-caches.tar') as t:
            names = t.getnames()
        self.assertIn(STORE + '/0A2D2B5C-0000-3000-8000-000000000001.isdata', names)
        self.assertIn(SETUP_C + '/com.apple.iconservices/store.index', names)
        self.assertFalse([n for n in names if n.startswith(USER_C)])
        self.assertEqual(struct.unpack_from('<I', (a / 'user/store.index').read_bytes(), 8)[0], 7)
        b = json.loads((a / 'build.json').read_text())
        self.assertEqual(b['user_index']['record_size'], 172)
        self.assertEqual(b['system_indexes']['248']['records'], 5)
        # fonts
        a = self.made('font-registry')
        with tarfile.open(a / 'setup.tar') as t:
            self.assertIn(SETUP_C + '/com.apple.FontRegistry/fontregistry.user', t.getnames())
        user = a / 'user/fontregistry.user'
        self.assertEqual(user.read_bytes().count(b'/Users/benchtest'), 0)
        db = sqlite3.connect(user)
        self.assertEqual(db.execute('SELECT path_column, flags_column, bookmark_ref_column FROM dir_table '
                                    'WHERE domain_column = 1').fetchall(), [('/var/empty/Library/Fonts', 0, None)])
        db.close()
        for kind in ('shader-caches', 'icon-caches', 'font-registry'):
            sums = (self.made(kind) / 'SHA256SUMS').read_text()
            self.assertIn('build.json', sums)

    def test_system_module_cache_recorded_elsewhere_refused(self):
        h, r = self.build(system_pcm_path='/var/folders/1w/kdhd3ny95wgcvpl9r1l1lv5h0000gn/C/com.apple.SetupAssistant/com.apple.metalfe/3BFB57ANS780')
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('records module cache path', r.stderr)

    def test_stale_icon_index_refused(self):
        _, r = self.build(user_index_build='26B123')
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('__cache_info__ says build 26B123', r.stderr)

    def test_compare(self):
        h, r = self.build()
        self.assertEqual(r.returncode, 0, r.stderr)
        pack = self.made('shader-caches') / 'data.tar'
        same = run('compare', pack, h.path)
        self.assertEqual(same.returncode, 0, same.stdout + same.stderr)
        self.assertIn('0 of 6 harvested Metal files new or changed', same.stdout)
        # a later boot's harvest whose Setup functions.data grew is a miss in that tree
        grown = self.d / 'grown.tar'
        with tarfile.open(h.path) as src, tarfile.open(grown, 'w') as dst:
            for m in src:
                data = src.extractfile(m).read() if m.isfile() else None
                if m.name.endswith('SetupAssistant/com.apple.metal/0_0/functions.data'):
                    data += b'more'
                    m.size = len(data)
                dst.addfile(m, io.BytesIO(data) if data is not None else None)
        diff = run('compare', pack, grown)
        self.assertEqual(diff.returncode, 1)
        self.assertIn('changed functions.data', diff.stdout)


class CollectTest(unittest.TestCase):
    def test_fixture_tree(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / 'root'
            metal = root / SETUP_C / 'WindowServer/com.apple.WindowServer/com.apple.metal/0_0'
            metal.mkdir(parents=True)
            (metal / 'functions.data').write_bytes(b'x')
            (root / USER_C / 'com.apple.iconservices').mkdir(parents=True)
            (root / USER_C / 'com.apple.iconservices/store.index').write_bytes(index(1))
            (root / STORE).mkdir(parents=True)
            (root / STORE / 'a.isdata').write_bytes(b'u')
            out = Path(d) / 'h.tar'
            with open(out, 'wb') as f:
                r = subprocess.run(['sh', str(COLLECT)], stdout=f, stderr=subprocess.PIPE, text=True,
                                   env=dict(os.environ, SEED_ROOT=str(root), SEED_NO_LOG='1'), cwd=d)
            self.assertEqual(r.returncode, 0, r.stderr)
            with tarfile.open(out) as t:
                names = t.getnames()
                folders = t.extractfile('diag/folders.txt').read().decode()
                dirs = t.extractfile('diag/cache-dirs.tsv').read().decode()
            self.assertIn(SETUP_C + '/WindowServer/com.apple.WindowServer/com.apple.metal/0_0/functions.data', names)
            self.assertIn(USER_C + '/com.apple.iconservices/store.index', names)
            self.assertIn(STORE + '/a.isdata', names)
            self.assertIn(' %s/WindowServer/com.apple.WindowServer\n' % SETUP_C, folders)
            self.assertIn('\t%s\n' % USER_C, dirs)
            for f in ('system.txt', 'metal-files.txt', 'icons.txt', 'hao.txt', 'ps.txt'):
                self.assertIn('diag/' + f, names)


if __name__ == '__main__':
    unittest.main()
