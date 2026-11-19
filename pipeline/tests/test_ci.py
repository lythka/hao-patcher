"""pipeline/ci: the checks pass on the committed tree and fail on a planted bad recipe, set or edit spec."""
import contextlib
import io
import json
from pathlib import Path
import runpy
import shutil
import subprocess
import tempfile
import unittest

TOP = Path(__file__).resolve().parents[2]
CI = runpy.run_path(str(TOP / 'pipeline' / 'ci'))


def committed_tree(where):
    """HEAD's tracked files (what pipeline/ci checks), copied from this checkout's working tree under WHERE."""
    files = subprocess.run(['git', 'ls-files', '-z'], cwd=TOP, capture_output=True, check=True).stdout.split(b'\0')
    for f in filter(None, files):
        src, dst = TOP / f.decode(), Path(where) / f.decode()
        if src.is_file() or src.is_symlink():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst, follow_symlinks=False)
    return Path(where)


class Planted(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = tempfile.TemporaryDirectory()
        cls.tree = committed_tree(cls.d.name)

    @classmethod
    def tearDownClass(cls):
        cls.d.cleanup()

    def planted(self, rel, edit):
        """Run with REL rewritten by EDIT, then restored."""
        p = self.tree / rel
        saved = p.read_bytes()
        p.write_text(edit(saved.decode()))
        self.addCleanup(p.write_bytes, saved)

    def test_clean_tree_passes_layout_and_sets(self):
        for check in (CI['check_layout'], CI['check_sets']):
            ok, out = check(self.tree, {})
            self.assertTrue(ok, out)

    def test_recipe_without_a_pack_fails_layout(self):
        def drop_pack(text):
            j = json.loads(text)
            del j['pack']
            return json.dumps(j)
        self.planted('patches/golden-gate-hello/patch.json', drop_pack)
        ok, out = CI['check_layout'](self.tree, {})
        self.assertFalse(ok)
        self.assertIn('golden-gate-hello', out)

    def test_set_that_differs_from_its_profile_fails_sets(self):
        self.planted('pipeline/sets/golden-gate-26B131.txt',
                     lambda t: ''.join(l for l in t.splitlines(True) if not l.startswith('golden-gate-hello ')))
        ok, out = CI['check_sets'](self.tree, {})
        self.assertFalse(ok)
        self.assertIn('golden-gate-26B131', out)

    def test_bad_edit_spec_fails_edit_specs(self):
        spec = sorted(self.tree.glob('patches/*/edits/*.json'))[0]
        self.planted(str(spec.relative_to(self.tree)), lambda t: json.dumps(dict(json.loads(t), surprise=1)))
        ok, out = CI['check_edit_specs'](self.tree, {})
        self.assertFalse(ok, out)

    def test_suites_cover_every_test_folder_and_no_store(self):
        found = CI['suites'](self.tree)
        for d in ('pipeline/tests', 'tools/hao/tests', 'tools/edit-engine/tests', 'cache/metallib/lib'):
            self.assertIn(d, found)
        self.assertFalse([d for d in found if d.startswith('store')])

    def test_every_tracked_test_file_is_in_a_suite(self):
        # a test folder no pattern finds would never run while CI passes (tools/*/*/tests were missed)
        found = CI['suites'](self.tree)
        files = subprocess.run(['git', 'ls-files'], cwd=TOP, capture_output=True, text=True, check=True).stdout.split()
        folders = {str(Path(f).parent) for f in files if Path(f).name.startswith('test') and f.endswith('.py')}
        missed = sorted(d for d in folders if not any(d == s or d.startswith(s + '/') for s in found))
        self.assertEqual(missed, [])
        for d in ('tools/metal-wrapper/spec/tests', 'tools/tahoe-donors/cache-dylib/tests'):
            self.assertIn(d, found)

    def test_network_is_closed_but_local_servers_are_not(self):
        e = CI['env']()
        self.assertEqual(e['https_proxy'], CI['CLOSED'])
        self.assertIn('127.0.0.1', e['no_proxy'])

    def failing_doctor(self):
        """tools/hao/hao replaced by one whose doctor reports a failed check and exits 5, as on an unready Mac."""
        event = json.dumps(dict(ev='doctor.check', name='python', state='fail', detail='hao runs on Python 3.9',
                                fix='run it with python3.14'))
        self.planted('tools/hao/hao', lambda t: 'import sys\nprint(%r)\nsys.exit(5)\n' % event)

    def test_doctor_runs_in_the_tree(self):
        ok, out = CI['check_doctor'](self.tree)
        self.assertTrue(ok, out)
        self.assertIn('python', out)
        self.failing_doctor()
        ok, out = CI['check_doctor'](self.tree)
        self.assertFalse(ok)
        self.assertIn('exit 5', out)
        self.assertIn('FAIL python    hao runs on Python 3.9  (run it with python3.14)', out)

    def test_a_failing_doctor_stops_the_run(self):
        self.failing_doctor()
        g = CI['main'].__globals__
        saved = g['export'], g['unexport']
        g['export'], g['unexport'] = (lambda sha, where: self.tree), (lambda where: None)
        self.addCleanup(g.update, export=saved[0], unexport=saved[1])
        with tempfile.TemporaryDirectory() as d:
            log = Path(d) / 'ci.json'
            with contextlib.redirect_stdout(io.StringIO()) as out:
                self.assertEqual(CI['main'](['HEAD', '--log', str(log)]), 1)
            self.assertIn('no other check ran', out.getvalue())
            j = json.loads(log.read_text())
        self.assertFalse(j['passed'])
        self.assertEqual(j['failed'], ['doctor'])
        self.assertEqual(list(j['checks']), ['doctor'])          # nothing else ran
        self.assertIn('hao runs on Python 3.9', j['checks']['doctor']['tail'])


if __name__ == '__main__':
    unittest.main()
