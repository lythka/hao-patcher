"""profile.py: each profiles/NAME.json is what OCLP's own patch-set detection gives pipeline/targets/NAME.json.

Needs OCLP_SOURCE (the unpacked row oclp_source), OCLP_SITE (the kit's site packages) and OCLP_PYTHON (the kit's
x86_64 Python 3.12); skips otherwise. The fixture-free checks always run."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

HERE = Path(__file__).resolve().parents[1]
TOP = HERE.parents[1]
ENV = ('OCLP_SOURCE', 'OCLP_SITE', 'OCLP_PYTHON')


class Profiles(unittest.TestCase):
    def test_every_profile_has_its_target(self):
        for p in sorted((HERE/'profiles').glob('*.json')):
            profile, target = json.loads(p.read_text()), TOP/'pipeline/targets'/p.name
            self.assertTrue(target.is_file(), p.name)
            self.assertEqual(profile['model'], json.loads(target.read_text())['model'])
            self.assertEqual(profile['name'], '%s-legacy-hardware-darwin%d' % (p.stem,
                                                                               profile['donor_policy']['darwin_major']))
            ops = [(e['patch'], e['operation'], e['destination'].rsplit('/', 1)[0], e['destination'].rsplit('/', 1)[1],
                    e['source_version']) for e in profile['entries']]
            want = [(n, op, d, f, v) for n, ops_ in profile['patches'].items() for op, ds in ops_.items()
                    for d, fs in ds.items() for f, v in fs.items()]
            self.assertEqual(ops, want, p.name)
            self.assertEqual(list(profile['patches']), profile['patchset_order'], p.name)

    @unittest.skipUnless(all(os.environ.get(k) for k in ENV), 'needs %s' % ', '.join(ENV))
    def test_profiles_are_oclps_detection(self):
        for p in sorted((HERE/'profiles').glob('*.json')):
            with tempfile.TemporaryDirectory() as d:
                out = Path(d)/p.name
                subprocess.run([os.environ['OCLP_PYTHON'], '-I', str(HERE/'profile.py'),
                                '--source', os.environ['OCLP_SOURCE'], '--site', os.environ['OCLP_SITE'],
                                '--target', str(TOP/'pipeline/targets'/p.name), '--like', str(HERE/'profiles/mbp92.json'),
                                str(out)], check=True, capture_output=True)
                self.assertEqual(out.read_bytes(), p.read_bytes(), p.name)


if __name__ == '__main__':
    unittest.main()
