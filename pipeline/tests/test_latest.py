"""pipeline/latest: picks the newest finished hao-runtime artifact, rewrites the row, bumps the patches that use it."""
import importlib.machinery, importlib.util, json, os, tempfile, time, unittest, contextlib, io

TOP = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_loader = importlib.machinery.SourceFileLoader('latest', os.path.join(TOP, 'pipeline', 'latest'))
_spec = importlib.util.spec_from_loader('latest', _loader)
latest = importlib.util.module_from_spec(_spec)
_loader.exec_module(latest)

ROW = ('# hao runtime payload for 26B131 (build.json names its commit: aaaaaaa).\n'
       '# AOT for 969 paths bound to libhao\n# 11111111. Async provider.\n'
       '- store/artifacts/hao-runtime-26B131-20261110-aaaaaaaaaaaa/payload\n')
README = '# p\n\n## Verify\nx\n\n## Validation\ny\n\n## Revisions\n- r4 2026-11-10 older\n'


class Latest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.top = self.tmp.name
        d = os.path.join(self.top, 'pipeline', 'inputs', 'golden', '26B131')
        os.makedirs(d)
        self.row = os.path.join(d, 'hao_runtime')
        open(self.row, 'w').write(ROW)
        for name, inputs in (('user-a', ['hao_runtime', 'x']), ('user-b', ['hao_runtime']), ('other', ['y'])):
            p = os.path.join(self.top, 'patches', name)
            os.makedirs(p)
            json.dump({'revision': 4, 'inputs': inputs}, open(os.path.join(p, 'patch.json'), 'w'))
            open(os.path.join(p, 'README.md'), 'w').write(README)

    def tearDown(self):
        self.tmp.cleanup()

    def art(self, name, commit, dirty=False, rc='rc=0', age=0):
        a = os.path.join(self.top, 'store', 'artifacts', name)
        os.makedirs(os.path.join(a, 'payload'))
        bj = os.path.join(a, 'payload', 'build.json')
        json.dump({'hao_commit': commit, 'hao_dirty': dirty}, open(bj, 'w'))
        open(os.path.join(a, 'build.log'), 'w').write('building\n%s\n' % rc)
        t = time.time() - age
        os.utime(bj, (t, t))
        return a

    def run_latest(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = latest.main(list(argv), top=self.top, today='2026-11-12')
        return rc, out.getvalue()

    def test_picks_newest_finished_and_bumps_users(self):
        self.art('hao-runtime-26B131-20261110-aaaaaaaaaaaa', 'aaaaaaa', age=300)
        self.art('hao-runtime-26B131-20261111-bbbbbbbbbbbb', 'bbbbbbb', age=100)
        self.art('hao-runtime-26B131-20261112-cccccccccccc', 'ccccccc', rc='still building', age=0)
        self.art('hao-runtime-26B131-20261112-dddddddddddd', 'ddddddd', dirty=True, age=0)
        rc, out = self.run_latest('hao_runtime', '26B131')
        self.assertEqual(rc, 0)
        row = open(self.row).read()
        self.assertTrue(row.endswith('- store/artifacts/hao-runtime-26B131-20261111-bbbbbbbbbbbb/payload\n'), row)
        self.assertIn('names its commit: bbbbbbb', row)
        self.assertEqual(row.count('\n- '), 1)
        for name, rev in (('user-a', 5), ('user-b', 5), ('other', 4)):
            self.assertEqual(json.load(open(os.path.join(self.top, 'patches', name, 'patch.json')))['revision'], rev)
        readme = open(os.path.join(self.top, 'patches', 'user-a', 'README.md')).read()
        self.assertIn('## Revisions\n- r5 2026-11-12 Input row `hao_runtime` (26B131) now names the payload built with '
                      'hao-runtime bbbbbbb', readme)
        self.assertNotIn('r5', open(os.path.join(self.top, 'patches', 'other', 'README.md')).read())

    def test_up_to_date_changes_nothing(self):
        self.art('hao-runtime-26B131-20261110-aaaaaaaaaaaa', 'aaaaaaa')
        rc, out = self.run_latest('hao_runtime', '26B131')
        self.assertEqual(rc, 0)
        self.assertIn('already names', out)
        self.assertEqual(open(self.row).read(), ROW)
        self.assertEqual(json.load(open(os.path.join(self.top, 'patches', 'user-a', 'patch.json')))['revision'], 4)

    def test_dry_run_writes_nothing(self):
        self.art('hao-runtime-26B131-20261111-bbbbbbbbbbbb', 'bbbbbbb')
        rc, out = self.run_latest('hao_runtime', '26B131', '--dry-run')
        self.assertEqual(rc, 0)
        self.assertIn('bbbbbbbbbbbb', out)
        self.assertEqual(open(self.row).read(), ROW)

    def test_no_finished_artifact_stops(self):
        self.art('hao-runtime-26B131-20261111-bbbbbbbbbbbb', 'bbbbbbb', rc='failed')
        with self.assertRaises(SystemExit):
            self.run_latest('hao_runtime', '26B131')


if __name__ == '__main__':
    unittest.main()
