"""docs/layout.md: recipes declare their pack, sets come from pipeline/order.txt + a profile, the check enforces it."""
import json
import os
from pathlib import Path
import runpy
import shutil
import tempfile
import unittest

TOP = Path(__file__).resolve().parents[2]
P = runpy.run_path(str(TOP / 'pipeline' / 'patches.py'))


class RealTree(unittest.TestCase):
    def test_every_profile_generates_its_set(self):
        profiles = sorted((TOP / 'pipeline/profiles').glob('*.json'))
        self.assertTrue(profiles)
        for prof in profiles:
            with self.subTest(profile=prof.stem):
                self.assertEqual(P['generate'](str(prof)), P['names'](TOP / 'pipeline/sets' / (prof.stem + '.txt')))

    def test_layout_is_clean(self):
        self.assertEqual(P['layout_problems'](), [])

    def test_pack_members_come_from_the_recipes(self):
        packs = P['load_packs']()
        self.assertIn('golden-gate-metal-argbuf', packs['intel-hd4000']['recipes'])
        self.assertIn('golden-gate-userspace', packs['base']['recipes'])
        self.assertTrue(all('recipes' not in json.loads(f.read_text()) for f in (TOP / 'packs').glob('*.json')))

    def test_real_waivers_are_well_formed(self):
        # here, not in Fixture: Fixture's setUp makes every Mac detect only the base pack
        self.assertEqual(P['waiver_problems'](), [])


class Fixture(unittest.TestCase):
    """A small tree: two OS recipes (one with a variant), one GPU recipe, one opt-in dev recipe."""

    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.root = Path(self.d.name)
        (self.root / 'patches').mkdir()
        (self.root / 'packs').mkdir()
        for name, pack, extra in (('os-a', 'base', {}), ('os-b', 'base', {}),
                                  ('os-b-fold', 'base', {'variant': 'fold', 'replaces': 'os-b'}),
                                  ('gpu-a', 'gpu', {}), ('dev-a', 'dev', {})):
            d = self.root / 'patches' / name
            d.mkdir()
            (d / 'patch.json').write_text(json.dumps(dict({'revision': 1, 'pack': pack, 'stage': 'oclp-hook',
                                                          'status': 'product', 'validated': True}, **extra)))
        for name, d in (('base', {'layer': 'os', 'detect': {'any_intel': True}}),
                        ('gpu', {'layer': 'platform', 'kind': 'gpu', 'platform': 'p', 'detect': {'board_ids': ['X']}}),
                        ('dev', {'layer': 'dev', 'detect': {'any_intel': True}, 'opt_in': True})):
            (self.root / 'packs' / (name + '.json')).write_text(json.dumps(dict({'format': 1, 'phase': 'installer'}, **d)))
        (self.root / 'order.txt').write_text('os-a\nos-b\nos-b-fold\ngpu-a\ndev-a\n')
        self.packs = P['load_packs'](str(self.root / 'packs'), str(self.root / 'patches'))
        g = P['generate'].__globals__
        self.saved = g['detect_packs']
        g['detect_packs'] = lambda target, packs=None: ['base']        # this fixture's Mac detects only the OS pack

    def tearDown(self):
        P['generate'].__globals__['detect_packs'] = self.saved
        self.d.cleanup()

    def gen(self, **prof):
        return P['generate'](dict({'target': 'mbp92'}, **prof), str(self.root / 'patches'), self.packs,
                             str(self.root / 'order.txt'))

    def test_detected_named_and_variants(self):
        self.assertEqual(self.gen(), ['os-a', 'os-b'])
        self.assertEqual(self.gen(packs=['gpu']), ['os-a', 'os-b', 'gpu-a'])
        self.assertEqual(self.gen(recipes=['dev-a']), ['os-a', 'os-b', 'dev-a'])
        self.assertEqual(self.gen(variants=['fold']), ['os-a', 'os-b-fold'])
        with self.assertRaises(ValueError):
            self.gen(packs=['nope'])

    def test_check_refuses_undeclared_and_listed(self):
        layout = lambda: P['layout_problems'](str(self.root / 'patches'), str(self.root / 'packs'),
                                              str(self.root / 'order.txt'), str(self.root / 'none'))
        self.assertEqual(layout(), [])
        p = self.root / 'patches/gpu-a/patch.json'
        p.write_text(json.dumps({'revision': 1, 'stage': 'oclp-hook'}))
        self.assertTrue(any('gpu-a: needs "pack"' in x for x in layout()))
        p.write_text(json.dumps({'revision': 1, 'pack': 'gpu', 'stage': 'oclp-hook'}))
        (self.root / 'packs/gpu.json').write_text(json.dumps({'format': 1, 'layer': 'platform', 'recipes': ['gpu-a']}))
        bad = layout()
        self.assertTrue(any('lists recipes' in x for x in bad) and any('needs "kind"' in x for x in bad))
        (self.root / 'order.txt').write_text('os-a\nos-b\n')
        self.assertTrue(any('order.txt: gpu-a is missing' in x for x in layout()))

    def test_release_profiles_refuse_dev_and_diagnostic_recipes(self):
        profiles = self.root / 'profiles'
        profiles.mkdir()
        rel = lambda name: P['release_problems'](name, str(self.root / 'patches'), self.packs, str(profiles),
                                                   str(self.root / 'order.txt'), waivers=[])
        (profiles / 'plain.json').write_text(json.dumps({'target': 'mbp92', 'release': True}))
        self.assertEqual(rel('plain'), [])
        (profiles / 'unmarked.json').write_text(json.dumps({'target': 'mbp92'}))
        self.assertIn('not marked "release": true', rel('unmarked')[0])
        self.assertIn('not a profile', rel('nope')[0])
        (profiles / 'devpack.json').write_text(json.dumps({'target': 'mbp92', 'recipes': ['dev-a'], 'release': True}))
        self.assertEqual(rel('devpack'), ['profile devpack: dev-a is in a dev-layer pack'])
        f = self.root / 'patches/os-a/patch.json'
        f.write_text(json.dumps(dict(json.loads(f.read_text()), status='diagnostic')))
        self.assertEqual(rel('plain'), ['profile plain: os-a has status diagnostic'])

    def gate_fixture(self):
        profiles = self.root / 'profiles'
        profiles.mkdir()
        (profiles / 'plain.json').write_text(json.dumps({'target': 'mbp92', 'release': True}))
        (profiles / 'devonly.json').write_text(json.dumps({'target': 'mbp92'}))
        kw = dict(root=str(self.root / 'patches'), packs=self.packs, profiles=str(profiles),
                  order=str(self.root / 'order.txt'))
        rel = lambda waivers=(), gate=True: P['release_problems']('plain', gate=gate, waivers=list(waivers), **kw)
        wp = lambda waivers: P['waiver_problems'](waivers=waivers, **kw)
        return rel, wp

    def set_recipe(self, name, **fields):
        f = self.root / 'patches' / name / 'patch.json'
        f.write_text(json.dumps(dict(json.loads(f.read_text()), **fields)))

    WAIVER = {'profile': 'plain', 'recipe': 'os-a', 'revision': 1, 'decided': '2026-11-17',
              'reason': 'owner 2026-11-17: ships in the alpha unproven'}

    def test_release_gate_refuses_unvalidated_recipes(self):
        rel, _ = self.gate_fixture()
        self.assertEqual(rel(), [])
        self.set_recipe('os-a', validated=False)
        self.assertEqual(rel(), ['profile plain: os-a r1 is validated false (no owner waiver in '
                                 'pipeline/release-waivers.json)'])
        self.assertEqual(rel(gate=False), [])         # patches.py check leaves this to release-gate

    def test_release_gate_refuses_experiment_recipes(self):
        rel, _ = self.gate_fixture()
        self.set_recipe('os-b', status='experiment')
        self.assertEqual(rel(), ['profile plain: os-b r1 is status experiment (no owner waiver in '
                                 'pipeline/release-waivers.json)'])

    def test_a_matching_waiver_lets_the_recipe_ship(self):
        rel, wp = self.gate_fixture()
        self.set_recipe('os-a', validated=False, status='experiment')
        self.assertEqual(rel([self.WAIVER]), [])
        self.assertEqual(wp([self.WAIVER]), [])
        self.assertTrue(rel([dict(self.WAIVER, profile='other')]))      # a waiver covers only its profile

    def test_a_waiver_for_an_older_revision_is_stale(self):
        rel, wp = self.gate_fixture()
        self.set_recipe('os-a', validated=False, revision=2)
        self.assertTrue(rel([self.WAIVER]))                              # r1's waiver does not cover r2
        self.assertEqual(wp([self.WAIVER]), ['waiver 0 (plain/os-a): waives r1 but the recipe is r2 (stale: '
                                             'remove it, or the owner waives r2)'])

    def test_waivers_must_name_real_things_and_the_owner(self):
        _, wp = self.gate_fixture()
        self.set_recipe('os-a', validated=False)
        self.assertIn('is not a release profile', wp([dict(self.WAIVER, profile='devonly')])[0])
        self.assertIn('is not a release profile', wp([dict(self.WAIVER, profile='nope')])[0])
        self.assertIn('no recipe nope', wp([dict(self.WAIVER, recipe='nope')])[0])
        self.assertIn('is not in profile plain', wp([dict(self.WAIVER, recipe='gpu-a')])[0])
        self.assertIn("owner's decision", wp([dict(self.WAIVER, reason='it is fine')])[0])
        self.assertIn('has no reason', wp([{k: v for k, v in self.WAIVER.items() if k != 'reason'}])[0])
        self.assertIn('not needed', wp([dict(self.WAIVER, recipe='os-b')])[0])

    def test_real_release_profiles_are_clean_and_marked(self):
        self.assertEqual(P['release_profile_problems'](), [])
        for name in ('alpha-v0.1.0', 'alpha-v0.1.0-fold', 'golden-gate-26B131'):
            self.assertEqual(P['release_problems'](name, gate=False), [], name)
        self.assertTrue(P['release_problems']('dev-bs-test'))


if __name__ == '__main__':
    unittest.main()
