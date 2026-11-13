"""pipeline/release-cut --dry-run: every row of an assembly classified; refuses while any is not reproducible.
python3.14 -I -B -m unittest discover -s pipeline/tests -k release"""
import json
from pathlib import Path
import runpy
import subprocess
import sys
import unittest

TOP = Path(__file__).resolve().parents[2]
R = runpy.run_path(str(TOP / 'pipeline' / 'release-cut'))


class ReleaseCut(unittest.TestCase):
    def test_missing_rows_are_reported(self):
        self.assertEqual(R['classify']('nothing', None)[0], 'missing')
        self.assertEqual(R['classify']('gone', (TOP, ('0' * 64, str(TOP / 'store/artifacts/none/x'))))[0], 'missing')

    def test_assembly_rows_cover_the_set_and_donors(self):
        rows = [r for r, _ in R['rows_for'](TOP / 'pipeline/assemblies/mbp92-26B131.json')]
        for want in ('darwin27_kernel', 'hao_runtime', 'golden_gate_userspace', 'oclp_hardware', 'tahoe_native_donors'):
            self.assertIn(want, rows)
        self.assertEqual(len(rows), len(set(rows)))

    @unittest.skipUnless((TOP / 'store/artifacts').is_dir(), 'needs the store')
    def test_dry_run_on_the_real_store(self):
        r = subprocess.run([sys.executable, '-I', str(TOP / 'pipeline/release-cut'), '--dry-run',
                            str(TOP / 'pipeline/assemblies/mbp92-26B131.json'), '--json'], capture_output=True, text=True)
        classes = {x['class'] for x in json.loads(r.stdout)}
        self.assertLessEqual(classes, {'download', 'reproducible', 'reproducible-except', 'dirty', 'harvested',
                                       'unrecorded', 'missing'})
        self.assertEqual(r.returncode, 0 if classes <= {'download', 'reproducible', 'reproducible-except', 'harvested'} else 1)


class Reproductions(unittest.TestCase):
    def test_entries_name_their_artifact_and_a_known_commit(self):
        data = json.loads((TOP / 'pipeline/reproductions.json').read_text())
        for row, e in data['rows'].items():
            self.assertIn(e['result'], ('identical', 'except'), row)
            self.assertTrue(e['result'] != 'except' or e.get('except'), row)
            self.assertTrue(R['commit_known'](e['commit']), '%s: commit %s not found' % (row, e['commit']))

    def test_a_record_for_another_artifact_is_ignored(self):
        self.assertIsNone(R['reproduction']('io80211_compat', 'io80211-compat-some-other-build'))
        self.assertIsNotNone(R['reproduction']('io80211_compat', 'io80211-compat-26B131-20261111-c0558983229b'))


class Write(unittest.TestCase):
    @unittest.skipUnless((TOP / 'store/artifacts').is_dir(), 'needs the store')
    def test_cut_refused_draft_written(self):
        import tempfile
        out = Path(tempfile.mkdtemp()) / 'release.json'
        cmd = [sys.executable, '-I', str(TOP / 'pipeline/release-cut'), '--write', str(out),
               str(TOP / 'pipeline/assemblies/mbp92-26B131.json')]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode == 0:                         # every row reproducible: a real cut
            self.assertEqual(json.loads(out.read_text())['state'], 'cut')
            return
        self.assertIn('refused', r.stderr)
        self.assertFalse(out.exists())
        r = subprocess.run(cmd + ['--draft'], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        rel = json.loads(out.read_text())
        self.assertEqual(rel['state'], 'draft')
        self.assertTrue(rel['refusals'])
        self.assertIn('golden-gate-hello', rel['recipes'])
        self.assertTrue(all(d['url'] is None for d in rel['downloads']))   # never guessed


if __name__ == '__main__':
    unittest.main()
