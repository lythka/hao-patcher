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
        self.assertLessEqual(classes, {'download', 'reproducible', 'dirty', 'harvested', 'unrecorded', 'missing'})
        self.assertEqual(r.returncode, 0 if classes <= {'download', 'reproducible'} else 1)


if __name__ == '__main__':
    unittest.main()
