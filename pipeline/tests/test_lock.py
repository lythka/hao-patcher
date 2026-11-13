"""Behaviour of pipeline/lock.py (inputs locks, target files) and pipeline/run's arguments. python3 -m unittest discover -s pipeline/tests"""
import hashlib, json, os, subprocess, sys, tempfile, unittest
from pathlib import Path

TOP = Path(__file__).resolve().parents[2]
LOCK = [sys.executable, '-I', str(TOP / 'pipeline/lock.py')]


def lock(*args):
    return subprocess.run(LOCK + [str(a) for a in args], capture_output=True, text=True)


class Lock(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.f = self.d / 'payload'; self.f.write_bytes(b'abc')
        self.sha = hashlib.sha256(b'abc').hexdigest()

    def write(self, text):
        p = self.d / 'x.lock'; p.write_text(text); return p

    def test_rows_comments_and_hash(self):
        p = self.write('# header\nkdk %s %s   # trailing note\ntree - ~/somewhere\n' % (self.sha, self.f))
        self.assertEqual(lock('path', p, 'kdk').stdout.strip(), str(self.f))
        self.assertEqual(lock('path', p, 'tree').stdout.strip(), os.path.expanduser('~/somewhere'))

    def test_relative_path_is_from_the_repository(self):
        p = self.write('tree - store/inputs/tree\n')
        self.assertEqual(lock('path', p, 'tree').stdout.strip(), str(TOP / 'store/inputs/tree'))
        t = self.d / 't.json'
        t.write_text(json.dumps({'esp': {'path': 'store/esp.tar', 'sha256': '-'}}))
        self.assertEqual(lock('path', t, 'esp').stdout.strip(), str(TOP / 'store/esp.tar'))

    def test_wrong_hash_or_missing_row_stops(self):
        p = self.write('kdk %s %s\n' % ('0' * 64, self.f))
        r = lock('path', p, 'kdk'); self.assertNotEqual(r.returncode, 0); self.assertIn('is not', r.stderr)
        r = lock('path', p, 'nope'); self.assertNotEqual(r.returncode, 0); self.assertIn('no row nope', r.stderr)

    def test_lock_folder_one_file_per_row(self):
        d = self.d / 'lockdir'; d.mkdir()
        (d / 'kdk').write_text('# a comment\n%s %s   # note\n' % (self.sha, self.f))
        self.assertEqual(lock('path', d, 'kdk').stdout.strip(), str(self.f))
        (d / 'bad').write_text('only-one-field\n')
        self.assertNotEqual(lock('path', d, 'kdk').returncode, 0)

    def payload(self, **over):
        d = self.d / 'pl'; d.mkdir(exist_ok=True)
        (d / 'libhao.dylib').write_bytes(b'hao')
        m = {'version': '26B131-abc', 'commit': 'a' * 40, 'clean': True, 'os_build': '26B131', 'cache_uuid': 'U',
             'units_version': 'u', 'units_sha256': 'b' * 64, 'layout_version': 1,
             'files': {'libhao.dylib': hashlib.sha256(b'hao').hexdigest()}}
        m.update(over)
        (d / 'payload-manifest.json').write_text(json.dumps(m))
        return d

    def row(self, text):
        d = self.d / 'lockdir'; d.mkdir(exist_ok=True)
        (d / 'hao_runtime').write_text(text)
        return d

    def test_payload_row_checked_by_its_manifest(self):
        pl = self.payload()
        msha = hashlib.sha256((pl / 'payload-manifest.json').read_bytes()).hexdigest()
        d = self.row('# check: payload os_build=26B131 layout_version=1 manifest_sha256=%s\n- %s\n' % (msha, pl))
        r = lock('path', d, 'hao_runtime'); self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), str(pl))
        for text, why in (('# check: payload os_build=26A428 layout_version=1\n', 'OS build'),
                          ('# check: payload os_build=26B131 layout_version=2\n', 'install-layout'),
                          ('# check: payload os_build=26B131 layout_version=1 manifest_sha256=%s\n' % ('0' * 64), 'is not'),
                          ('# check: payload layout_version=1\n', 'needs os_build'),
                          ('# check: nonsense\n', 'unknown check'),
                          ('# check: payload os_build\n', 'bad check line')):
            r = lock('path', self.row(text + '- %s\n' % pl), 'hao_runtime')
            self.assertNotEqual(r.returncode, 0, text); self.assertIn(why, r.stderr)
        (pl / 'libhao.dylib').write_bytes(b'changed')
        r = lock('path', self.row('# check: payload os_build=26B131 layout_version=1\n- %s\n' % pl), 'hao_runtime')
        self.assertNotEqual(r.returncode, 0); self.assertIn('does not match', r.stderr)

    def test_payload_row_dirty_or_unchecked(self):
        pl = self.payload(clean=False)
        r = lock('path', self.row('# check: payload os_build=26B131 layout_version=1\n- %s\n' % pl), 'hao_runtime')
        self.assertNotEqual(r.returncode, 0); self.assertIn('dirty', r.stderr)
        r = lock('path', self.row('# a plain comment mentioning check\n- %s\n' % pl), 'hao_runtime')
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_duplicate_row_stops(self):
        r = lock('path', self.write('kdk - /a\nkdk - /b\n'), 'kdk')
        self.assertNotEqual(r.returncode, 0); self.assertIn('twice', r.stderr)

    def test_bad_row_stops(self):
        r = lock('path', self.write('kdk onlytwo\n'), 'kdk')
        self.assertNotEqual(r.returncode, 0); self.assertIn('bad row', r.stderr)

    def test_target_file(self):
        t = self.d / 't.json'
        t.write_text(json.dumps({'board_id': 'Mac-X', 'esp': {'path': str(self.f), 'sha256': self.sha},
                                 'oclp_hardware_profile': {'model': 'M'}}))
        self.assertEqual(lock('path', t, 'esp').stdout.strip(), str(self.f))
        self.assertEqual(lock('target', t, 'board_id').stdout.strip(), 'Mac-X')
        self.assertEqual(json.loads(lock('target', t, 'oclp_hardware_profile').stdout), {'model': 'M'})

    def test_kdk_name_and_psp(self):
        self.assertEqual(lock('kdk-name', '/k/Kernel_Debug_Kit_26.7.2_build_25G330.dmg').stdout.strip(), 'KDK_26.7.2_25G330.kdk')
        self.assertNotEqual(lock('kdk-name', '/k/other.dmg').returncode, 0)
        c = self.d / 'oclp/opencore_legacy_patcher'; c.mkdir(parents=True)
        (c / 'constants.py').write_text('        self.patcher_support_pkg_version:     str = "2.0.2"  # PSP\n')
        self.assertEqual(lock('psp', self.d / 'oclp').stdout.strip(), '2.0.2')


class Key(unittest.TestCase):
    """lock.py key: cache keys of what the inputs are, not of where the store is or of row comments."""
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())

    def store(self, where, payload=b'abc', folder='artifacts/thing-1234'):
        st = self.d / where / 'store'
        (st / folder).mkdir(parents=True); (st / folder / 'f').write_bytes(b'x')
        (st / 'k.bin').write_bytes(payload)
        return st

    def lockdir(self, name, rows):
        d = self.d / name; d.mkdir(parents=True, exist_ok=True)
        for n, text in rows.items():
            (d / n).write_text(text)
        return d

    def rows_for(self, st, comment='# note\n', sha=None):
        return {'kdk': '%s%s %s\n' % (comment, sha or hashlib.sha256(b'abc').hexdigest(), st / 'k.bin'),
                'tree': '%s- %s\n' % (comment, st / 'artifacts/thing-1234')}

    def key(self, st, locks, *items):
        r = lock('key', st, ':'.join(map(str, locks)), *items)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.strip()

    def test_store_move_and_comments_keep_the_key(self):
        a = self.store('old'); b = self.store('new')
        la = self.lockdir('a', self.rows_for(a)); lb = self.lockdir('b', self.rows_for(b, comment='# another note\n'))
        ka = self.key(a, [la], 'row:kdk', 'row:tree', 'text:x')
        self.assertRegex(ka, '^[0-9a-f]{12}$')
        self.assertEqual(ka, self.key(b, [lb], 'row:kdk', 'row:tree', 'text:x'))

    def test_content_changes_miss(self):
        st = self.store('s'); base = self.key(st, [self.lockdir('a', self.rows_for(st))], 'row:kdk', 'row:tree')
        other = self.lockdir('b', self.rows_for(st, sha=hashlib.sha256(b'abd').hexdigest()))
        self.assertNotEqual(base, self.key(st, [other], 'row:kdk', 'row:tree'))   # a new hash in the row
        (st / 'artifacts/thing-5678').mkdir()
        moved = self.lockdir('c', dict(self.rows_for(st), tree='- %s\n' % (st / 'artifacts/thing-5678')))
        self.assertNotEqual(base, self.key(st, [moved], 'row:kdk', 'row:tree'))   # a '-' row naming another artifact

    def test_dash_file_row_counts_by_content(self):
        st = self.store('s'); f = st / 'artifacts/one'; f.write_bytes(b'1')
        lk = self.lockdir('a', {'one': '- %s\n' % f})
        k1 = self.key(st, [lk], 'row:one'); f.write_bytes(b'2')
        self.assertNotEqual(k1, self.key(st, [lk], 'row:one'))

    def test_lock_order_and_missing_row(self):
        st = self.store('s')
        build = self.lockdir('build', {'kdk': '%s %s\n' % ('1' * 64, st / 'k.bin')})
        common = self.lockdir('common', self.rows_for(st))
        lines = lock('key-lines', st, '%s:%s' % (build, common), 'row:kdk', 'row:tree').stdout.splitlines()
        self.assertEqual(lines, ['row kdk ' + '1' * 64, 'row tree store/artifacts/thing-1234'])
        r = lock('key', st, str(common), 'row:nope')
        self.assertNotEqual(r.returncode, 0); self.assertIn('no row nope', r.stderr)
        r = lock('key', st, str(common), 'bogus:1')
        self.assertNotEqual(r.returncode, 0); self.assertIn('bad item', r.stderr)

    def test_target_json_counts_hashes_not_paths(self):
        st = self.store('s'); t = self.d / 'mbp.json'
        def write(path, sha='a' * 64, note='n', model='M'):
            t.write_text(json.dumps({'model': model, 'esp': {'path': path, 'sha256': sha, 'note': note}}, indent=1))
            return self.key(st, [], 'json:%s' % t)
        k = write('~/old/store/esp.tar')
        self.assertEqual(k, write('~/new/store/esp.tar', note='other note'))
        self.assertNotEqual(k, write('~/new/store/esp.tar', sha='b' * 64))
        self.assertNotEqual(k, write('~/new/store/esp.tar', model='N'))

    def test_patch_counts_revision_and_src(self):
        st = self.store('s')
        def patch(top, rev=1, body='echo hi\n'):
            d = self.d / top / 'patches/demo'; (d / 'src').mkdir(parents=True, exist_ok=True)
            (d / 'patch.json').write_text(json.dumps({'revision': rev, 'stage': 'oclp-hook'}))
            (d / 'src/hook.sh').write_text(body)
            return self.key(st, [], 'patch:%s' % d)
        k = patch('one')
        self.assertEqual(k, patch('two'))   # another checkout
        self.assertNotEqual(k, patch('one', rev=2))
        self.assertNotEqual(k, patch('one', body='echo bye\n'))

    def test_file_and_text_items(self):
        st = self.store('s'); f = self.d / 'stage.sh'; f.write_text('a')
        k = self.key(st, [], 'file:%s' % f, 'text:fd83 one\ntwo')
        self.assertEqual(k, self.key(st, [], 'file:%s' % f, 'text:fd83  one two'))
        f.write_text('b'); self.assertNotEqual(k, self.key(st, [], 'file:%s' % f, 'text:fd83 one two'))


class Repo(unittest.TestCase):
    def test_shipped_locks_and_targets_parse(self):
        inputs = TOP / 'pipeline/inputs'
        locks = [d for d in inputs.iterdir() if d.name != 'golden'] + sorted((inputs / 'golden').glob('*'))
        for d in locks:
            for f in d.iterdir():
                data = [l.split('#', 1)[0].split() for l in f.read_text().splitlines() if not l.lstrip().startswith('#')]
                self.assertEqual([len(x) for x in data if x], [2], '%s/%s' % (d.name, f.name))
        for t in (TOP / 'pipeline/targets').glob('*.json'):
            j = json.loads(t.read_text())
            self.assertTrue({'model', 'board_id', 'esp', 'oclp_hardware_profile'} <= set(j), t.name)

    def test_run_needs_four_arguments(self):
        r = subprocess.run(['zsh', str(TOP / 'pipeline/run'), 'mbp92', '25G332'], capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0); self.assertIn('OCLP', r.stderr)


if __name__ == '__main__':
    unittest.main()
