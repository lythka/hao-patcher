"""The native reference is offscreen Metal; never launch a GUI application."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

UNIT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(sys.platform == 'darwin' and shutil.which('xcrun'), 'Requires macOS and Xcode Metal tools')
class NativeReference(unittest.TestCase):
    def test_complete_pixel_matrix(self):
        with tempfile.TemporaryDirectory(prefix='metal-framebuffer-test-') as scratch:
            root = Path(scratch)
            bundle, results = root/'bundle', root/'results'
            subprocess.run([sys.executable, '-I', str(UNIT/'metal-framebuffer'), 'build', str(bundle)],
                           check=True, capture_output=True, text=True)
            signed = subprocess.run(['codesign', '--verify', str(bundle/'probe')], capture_output=True)
            self.assertEqual(signed.returncode, 0, signed.stderr)
            process = subprocess.run([str(bundle/'probe'), str(bundle/'probe.metallib'), str(results), 'all'],
                                     capture_output=True, text=True, timeout=180)
            rows = json.loads((results/'results.json').read_text())
            failures = [r for r in rows if r['status'] != 'pass']
            self.assertEqual(process.returncode, 0, json.dumps(failures[:8], indent=2)+process.stderr[-2000:])
            self.assertIn(len(rows), (720, 1440))
            self.assertFalse(failures)
            self.assertEqual({r['attachment'] for r in rows}, {0, 1, 2})
            self.assertEqual({r['form'] for r in rows}, {'one', 'draws', 'overlap', 'indexed', 'instanced', 'strip', 'indexed-strip', 'indexed-instanced', 'offset-indexed', 'offset-direct'})
            for row in rows:
                self.assertEqual((results/(row['case']+'.f32')).stat().st_size,
                                 row['compared_components']*4)
            for label, samples in [('single', 1), ('ms', 4)]:
                candidate = root/('lowered-'+label)
                process = subprocess.run([str(bundle/'probe'), str(bundle/('lowered-'+label+'.metallib')),
                    str(candidate), 'lowered-control', str(samples)], capture_output=True, text=True, timeout=180)
                rows = json.loads((candidate/'results.json').read_text())
                self.assertEqual(process.returncode, 0, process.stdout[-2000:]+process.stderr[-2000:])
                if samples == 1: self.assertEqual(len(rows), 360)
                self.assertTrue(all(r['status'] == 'pass' for r in rows))
                self.assertTrue(all(r['reflected_texture_index'] == 127 for r in rows))
            identities = root/'identities'
            process = subprocess.run([str(bundle/'probe'), str(bundle/'probe.metallib'), str(identities), 'native'],
                env={**os.environ, 'FB_PROBE_IDENTITIES': '1'}, capture_output=True, text=True, timeout=180)
            self.assertEqual(process.returncode, 0, process.stdout[-2000:]+process.stderr[-2000:])
            rows = json.loads((identities/'results.json').read_text())
            self.assertIn(len(rows), (30, 60))
            self.assertTrue(all(r['status'] == 'pass' for r in rows))


if __name__ == '__main__':
    unittest.main()
