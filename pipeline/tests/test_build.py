"""pipeline/build: an assembly picks its runner; --dry-run prints the command."""
import json, os, subprocess, sys, tempfile, unittest

TOP = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BUILD = os.path.join(TOP, 'pipeline', 'build')


def dry(*args):
    r = subprocess.run([sys.executable, '-I', BUILD, *args, '--dry-run'], capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr


class Build(unittest.TestCase):
    def test_tahoe_assembly_uses_the_tahoe_first_runner(self):
        rc, out = dry('tahoe-mbp92-25G342')
        self.assertEqual(rc, 0, out)
        self.assertIn('REPO=%s/store/images/mbp92-25G342-tahoe-dev zsh %s/pipeline/run mbp92 25G342-tahoe fd83bda09 tahoe-dev'
                      % (TOP, TOP), out)

    def test_golden_assembly_uses_golden_run_with_its_repo(self):
        rc, out = dry('dev-26B131')
        self.assertEqual(rc, 0, out)
        self.assertIn('pipeline/golden-run %s/pipeline/assemblies/dev-26B131.json --repo %s/store/images/mbp92-26B131-tested'
                      % (TOP, TOP), out)

    def test_repo_flag_overrides_and_latest_is_golden_only(self):
        rc, out = dry('tahoe-mbp92-25G342', '--repo', '/tmp/x')
        self.assertIn('REPO=/tmp/x ', out)
        rc, out = dry('tahoe-mbp92-25G342', '--latest')
        self.assertNotEqual(rc, 0)
        self.assertIn('golden assemblies', out)

    def test_every_assembly_names_a_known_runner(self):
        d = os.path.join(TOP, 'pipeline', 'assemblies')
        for f in os.listdir(d):
            if f.endswith('.json'):
                self.assertIn(json.load(open(os.path.join(d, f))).get('runner', 'golden'), ('golden', 'tahoe'), f)

    def test_missing_repo_stops(self):
        with tempfile.NamedTemporaryFile('w', suffix='.json', delete=False) as f:
            json.dump({'schema': 1, 'runner': 'tahoe', 'target': 'mbp92', 'os_build': 'x', 'oclp': 'y', 'patch_set': 'z'}, f)
        try:
            rc, out = dry(f.name)
            self.assertNotEqual(rc, 0)
            self.assertIn('no image repository', out)
        finally:
            os.unlink(f.name)


if __name__ == '__main__':
    unittest.main()
