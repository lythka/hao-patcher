"""Behaviour of pipeline/patches.py (sets, applies, stages). python3 -m unittest discover -s pipeline/tests"""
import json, subprocess, sys, tempfile, unittest
from pathlib import Path

TOP = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(TOP / 'pipeline'))
import patches


class Plan(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.target = self.d / 'm.json'; self.target.write_text(json.dumps({'model': 'MacBookPro9,2'}))

    def patch(self, name, **p):
        (self.d / name).mkdir(); (self.d / name / 'patch.json').write_text(json.dumps({'revision': 1, **p}))

    def plan(self, text, build='25G332'):
        s = self.d / 'set.txt'; s.write_text(text)
        return patches.plan(s, self.target, build, root=self.d)

    def test_set_order_comments_and_stages(self):
        self.patch('hook', applies='any', stage='oclp-hook')
        self.patch('data', applies='any', stage='post', inputs=['row_a', 'row_b'])
        self.patch('rec', applies='any', stage='recovery', inputs=['row_c'])
        self.assertEqual(self.plan('# a set\n\ndata   # trailing note\nhook\nrec\n'),
                         [('post', 'data', ['row_a', 'row_b']), ('oclp-hook', 'hook', []), ('recovery', 'rec', ['row_c'])])
        self.assertEqual(self.plan(''), [])

    def test_applies_by_model_and_build(self):
        self.patch('only332', applies={'builds': ['25G332']}, stage='post')
        self.patch('other', applies={'models': ['MacBookPro8,1']}, stage='post')
        self.assertEqual([n for _, n, _ in self.plan('only332\nother\n')], ['only332'])
        self.assertEqual(self.plan('only332\n', build='25G241'), [])

    def test_bad_patch_stops(self):
        self.patch('nostage', applies='any')
        self.patch('badkey', applies={'gpu': ['x']}, stage='post')
        self.patch('noapplies', stage='post')
        self.patch('badstage', applies='any', stage='s9')
        for n in ('nostage', 'badkey', 'noapplies', 'badstage'):
            with self.assertRaises(ValueError, msg=n):
                self.plan(n + '\n')
        with self.assertRaises(OSError):
            self.plan('missing\n')


class Repo(unittest.TestCase):
    def test_shipped_sets_plan_for_every_target_and_build(self):
        for s in (TOP / 'pipeline/sets').glob('*.txt'):
            for t in (TOP / 'pipeline/targets').glob('*.json'):
                for lock in [d for d in (TOP / 'pipeline/inputs').iterdir() if d.is_dir() and d.name.startswith('25')]:
                    r = subprocess.run([sys.executable, '-I', str(TOP / 'pipeline/patches.py'), 'plan', str(s), str(t),
                                        lock.name], capture_output=True, text=True)
                    self.assertEqual(r.returncode, 0, '%s %s %s: %s' % (s.name, t.name, lock.name, r.stderr))
        self.assertEqual(patches.names(TOP / 'pipeline/sets/stock.txt'), [])

    def test_every_patch_folder_has_its_recipe(self):
        # oclp-hook: src/hook.sh or src/kc-hook.sh; post and recovery: apply.sh; inputs are lock rows
        rows = set()
        for lock in (TOP / 'pipeline/inputs').iterdir():
            rows |= {f.name for f in lock.iterdir() if f.is_file()}
        for lock in (TOP / 'pipeline/inputs/golden').iterdir():   # golden-run reads inputs/golden/<build> first
            rows |= {f.name for f in lock.iterdir() if f.is_file()}
        for pj in (TOP / 'patches').glob('*/patch.json'):
            p, d = json.loads(pj.read_text()), pj.parent
            if p.get('status') == 'unfinished':   # no recipe yet, in no set (patches.check keeps it out of shipping)
                continue
            if p['stage'] == 'oclp-hook':
                self.assertTrue((d / 'src/hook.sh').is_file() or (d / 'src/kc-hook.sh').is_file(), d.name)
            else:
                self.assertTrue((d / 'apply.sh').is_file(), d.name)
            for r in p.get('inputs', []):
                self.assertIn(r, rows, d.name)
            # the README's newest "Revisions" line is the patch.json revision
            rev = (d / 'README.md').read_text().split('\n## Revisions\n', 1)[-1]
            self.assertIn('\n- r%d ' % p['revision'], '\n' + rev, d.name)


    def test_tracker_is_current(self):
        # patches/TRACKER.md is generated; regenerate with `python3 -I pipeline/patches.py tracker`
        import patches
        self.assertEqual((TOP / 'patches/TRACKER.md').read_text(), patches.tracker(), 'patches/TRACKER.md is stale')


class Format(unittest.TestCase):
    """patches.check: patch.json keys and values, required README sections, Validation agrees with "validated",
    shipping sets select product patches only."""
    README = '# x\n\n## Verify\nrun it\n\n## Validation\n%s\n\n## Revisions\n- r1 2026-11-02 new (abc)\n'

    def setUp(self):
        self.d = Path(tempfile.mkdtemp()); self.sets = self.d / 'sets'; self.sets.mkdir()
        (self.d / 'patches').mkdir()

    def patch(self, name, validation='In use.', **p):
        d = self.d / 'patches' / name; d.mkdir()
        (d / 'patch.json').write_text(json.dumps({'revision': 1, 'stage': 'post', 'applies': 'any',
                                                  'status': 'product', 'validated': True, **p}))
        (d / 'README.md').write_text(self.README % validation)

    def check(self, shipping=''):
        (self.sets / 'recovery.txt').write_text(shipping)
        return patches.check(self.d / 'patches', self.sets)

    def test_a_patch_in_the_format_passes(self):
        self.patch('ok'); self.patch('wip', validation='NEEDS VALIDATION: OPEN.', validated=False)
        self.assertEqual(self.check('ok\nwip\n'), [])

    def test_each_rule(self):
        self.patch('nostatus', status='later'); self.patch('mismatch', validation='NEEDS VALIDATION.')
        self.patch('devship', status='dev'); self.patch('oldrev', revision=2)
        (self.d / 'patches' / 'nojson').mkdir()
        got = '\n'.join(self.check('devship\n'))
        for want in ('nostatus: status', 'mismatch: patch.json validated=True', 'devship: status dev, but set recovery',
                     'oldrev: newest Revisions line is r1', 'nojson: no patch.json'):
            self.assertIn(want, got)

    def test_repo_patches_follow_the_format(self):
        self.assertEqual(patches.check(), [])


if __name__ == '__main__':
    unittest.main()
