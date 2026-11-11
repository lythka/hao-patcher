"""pipeline/lock.py oclp-keys: the oclp stage's cache points (whole stage, and before each kc-hook) and which one a run
resumes from. python3 -m unittest discover -s pipeline/tests"""
import json, os, subprocess, sys, tempfile, unittest
from pathlib import Path

TOP = Path(__file__).resolve().parents[2]
LOCK = [sys.executable, '-I', str(TOP / 'pipeline/lock.py')]
BASE = ['text:pristine0 oclp0', 'row:kdk']


class OclpKeys(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.store = self.d / 'store'; (self.store / 'artifacts').mkdir(parents=True)
        self.locks = self.d / 'lock'; self.locks.mkdir()
        self.row('kdk', b'kdk')
        self.patches = self.d / 'patches'
        self.cache = self.d / 'store/cache/oclp-prefix'

    def row(self, name, content):
        f = self.store / 'artifacts' / name; f.write_bytes(content)
        (self.locks / name).write_text('- %s\n' % f)

    def patch(self, name, files=('kc-hook.sh',), rev=1, body='true\n'):
        d = self.patches / name; (d / 'src').mkdir(parents=True, exist_ok=True)
        (d / 'patch.json').write_text(json.dumps({'revision': rev, 'stage': 'oclp-hook'}))
        for f in files:
            (d / 'src' / f).write_text(body)

    def keys(self, hooks):
        """{'full': KEY, NAME: KEY for the point before kc-hook NAME, 'resume': NAME or None}"""
        r = subprocess.run(LOCK + ['oclp-keys', str(self.store), str(self.locks), str(self.patches),
                                   str(self.cache / 'mbp92-25G332-{key}')] + BASE,
                           input=''.join('oclp-hook %s\n' % h for h in hooks), capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        out = {'resume': None}
        for line in r.stdout.splitlines():
            f = line.split()
            if f[0] == 'full':
                out['full'] = f[1]
            elif f[0] == 'before':
                self.assertEqual(f[3], str(self.cache / ('mbp92-25G332-' + f[2])))
                out[f[1]] = f[2]
            else:
                out['resume'] = f[1]
        return out

    def golden_gate(self):
        """golden-gate's shape: a hook.sh patch and three kc-hooks, the last with an input row."""
        self.patch('metal', files=('hook.sh',))
        for n in ('kernel', 'userspace', 'hao'):
            self.patch(n)
        self.row('hao_runtime', b'hao 1')
        return ['metal', 'kernel', 'userspace', 'hao hao_runtime']

    def test_points_in_set_order(self):
        k = self.keys(self.golden_gate())
        self.assertEqual(set(k), {'full', 'kernel', 'userspace', 'hao', 'resume'})
        self.assertEqual(len({k['full'], k['kernel'], k['userspace'], k['hao']}), 4)
        self.assertRegex(k['hao'], '^[0-9a-f]{12}$')

    def test_later_kc_hook_change_keeps_earlier_points(self):
        hooks = self.golden_gate(); a = self.keys(hooks)
        self.row('hao_runtime', b'hao 2')   # only the hao payload changed (P3's case)
        b = self.keys(hooks)
        self.assertNotEqual(a['full'], b['full'])
        self.assertEqual([a[n] for n in ('kernel', 'userspace', 'hao')], [b[n] for n in ('kernel', 'userspace', 'hao')])
        self.patch('userspace', rev=2)   # a middle kc-hook: the points after it change, the ones before it do not
        c = self.keys(hooks)
        self.assertEqual([b['kernel'], b['userspace']], [c['kernel'], c['userspace']])
        self.assertNotEqual(b['hao'], c['hao']); self.assertNotEqual(b['full'], c['full'])

    def test_hook_sh_change_changes_every_point(self):
        hooks = self.golden_gate(); a = self.keys(hooks)
        self.patch('metal', files=('hook.sh',), body='echo changed\n')
        b = self.keys(hooks)
        for n in ('full', 'kernel', 'userspace', 'hao'):
            self.assertNotEqual(a[n], b[n], n)

    def test_patch_with_hook_sh_and_kc_hook_counts_in_every_point(self):
        """OCLP ran its hook.sh before every point; its kc-hook is still a point boundary of its own."""
        self.patch('a'); self.patch('both', files=('hook.sh', 'kc-hook.sh')); self.patch('b')
        hooks = ['a', 'both', 'b']; k = self.keys(hooks)
        self.assertEqual(len({k['full'], k['a'], k['both'], k['b']}), 4)   # before 'both' != after it
        self.patch('both', files=('hook.sh', 'kc-hook.sh'), rev=2)
        k2 = self.keys(hooks)
        for n in ('full', 'a', 'both', 'b'):
            self.assertNotEqual(k[n], k2[n], n)

    def test_later_kc_hook_only_patches_do_not_count(self):
        """Adding or removing a later kc-hook keeps the points before it: sets that reach the same state share it."""
        hooks = self.golden_gate(); self.patch('tahoe')
        gg = self.keys(hooks)
        other = self.keys(['metal', 'kernel', 'tahoe'])   # the same state before its second kc-hook
        self.assertEqual(gg['kernel'], other['kernel'])
        self.assertEqual(gg['userspace'], other['tahoe'])
        self.assertNotEqual(gg['full'], other['full'])

    def test_resume_is_the_last_cached_point(self):
        hooks = self.golden_gate(); k = self.keys(hooks)
        self.assertIsNone(k['resume'])
        def cache(name):
            d = self.cache / ('mbp92-25G332-' + k[name]); d.mkdir(parents=True); (d / 'target.img').write_bytes(b'')
        cache('kernel'); self.assertEqual(self.keys(hooks)['resume'], 'kernel')
        cache('hao'); self.assertEqual(self.keys(hooks)['resume'], 'hao')   # the longest unchanged prefix wins
        (self.cache / ('mbp92-25G332-' + k['userspace'])).mkdir()   # a folder without target.img (a cut save) is no point
        self.assertEqual(self.keys(hooks)['resume'], 'hao')
        self.row('hao_runtime', b'hao 2'); self.assertEqual(self.keys(hooks)['resume'], 'hao')
        self.patch('userspace', rev=2); self.assertEqual(self.keys(hooks)['resume'], 'kernel')

    def test_no_kc_hooks_only_the_whole_stage(self):
        self.patch('metal', files=('hook.sh',))
        self.assertEqual(set(self.keys(['metal'])), {'full', 'resume'})
        self.assertEqual(set(self.keys([])), {'full', 'resume'})

    def test_shipped_set_points(self):
        """golden-gate: a point before each of its kc-hooks (the plan, not the cache); every recipe is a kc-hook."""
        plan = subprocess.run([sys.executable, '-I', str(TOP / 'pipeline/patches.py'), 'plan',
                               str(TOP / 'pipeline/sets/golden-gate.txt'), str(TOP / 'pipeline/targets/mbp92.json'),
                               '25G332'], capture_output=True, text=True, check=True).stdout
        hooks = [l for l in plan.splitlines() if l.startswith('oclp-hook ')]
        sys.path.insert(0, str(TOP / 'pipeline'))
        try:
            import lock
        finally:
            sys.path.pop(0)
        points = lock.oclp_points(hooks, str(TOP / 'patches'))
        self.assertEqual([p[0] for p in points], [None, 'darwin27-kernel', 'golden-gate-userspace',
                                                  'darwin27-sandbox-disk-space', 'hao-runtime',
                                                  'golden-gate-metal-libraries', 'golden-gate-hello',
                                                  'golden-gate-windowserver-filter', 'golden-gate-corespotlight-compat',
                                                  'metalold-selectors', 'golden-gate-metal-framebuffer',
                                                  'golden-gate-code-identity'])
        self.assertEqual(points[4][1], ['darwin27-kernel', 'golden-gate-userspace', 'darwin27-sandbox-disk-space'])
        # No recipe has a hook.sh any more, so the state before hao-runtime holds exactly the kc-hooks before it.
        self.assertEqual([l.split()[1] for l in points[4][2]],
                         ['darwin27-kernel', 'golden-gate-userspace', 'darwin27-sandbox-disk-space'])

if __name__ == '__main__':
    unittest.main()
