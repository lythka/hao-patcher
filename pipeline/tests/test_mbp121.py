"""The MacBookPro12,1 target (Broadwell, Iris 6100): detection, its generated set and plan, and that nothing for Ivy
Bridge (CPU or GPU) reaches it. Facts: OCLP's model data and the 12,1's recorded facts."""
import contextlib
import io
import json
from pathlib import Path
import runpy
import tempfile
import unittest

TOP = Path(__file__).resolve().parents[2]
P = runpy.run_path(str(TOP / 'pipeline' / 'patches.py'))
TARGET = TOP / 'pipeline/targets/mbp121.json'
BUILD = '26B131/25G342'
IVY_PACKS = ('mbp92', 'recovery-mbp92', 'intel-hd4000', 'metal-3802', 'metal-3802-extras', 'metal27-trial')


def recipe(name):
    return json.loads((TOP / 'patches' / name / 'patch.json').read_text())


def plan(names, packs=None):
    with tempfile.TemporaryDirectory() as d, contextlib.redirect_stderr(io.StringIO()):
        s = Path(d) / 'set.txt'
        s.write_text(''.join(n + '\n' for n in names))
        return [n for _, n, _ in P['plan'](str(s), str(TARGET), BUILD, packs=packs)]


class Mbp121(unittest.TestCase):
    def test_facts(self):
        f = P['target_facts'](str(TARGET))
        self.assertEqual(f['gpu'], {'intel-bdw'})
        self.assertEqual(f['firmware'], 'none')
        for feature in ('avx2', 'fma', 'bmi1', 'bmi2', 'movbe', 'smap', 'smep', 'invpcid'):
            self.assertIn(feature, f['cpu'])                    # the 12,1's facts: over Ivy Bridge
        self.assertNotIn('rtm', f['cpu'])                       # TSX unknown until a capture from the Mac (QEMU Broadwell-noTSX)

    def test_oclp_patch_sets(self):
        # OCLP classes Broadwell as Metal 31001: none of the 3802 sets, which select metal-3802
        sets = P['oclp_patchsets'](str(TARGET))
        self.assertIn('Intel Broadwell', sets)
        self.assertFalse({s for s in sets if '3802' in s or 'Ivy' in s})

    def test_detected_packs(self):
        self.assertEqual(sorted(P['detect_packs'](str(TARGET))),
                         ['audio-modern', 'base', 'mbp121', 'wifi-broadcom-modern'])

    def test_set_has_no_ivy_bridge_recipe(self):
        """The cpu-ivybridge rule (R6c) as data: no recipe that needs a CPU without AVX2 or an Ivy Bridge GPU, and no
        recipe of an Ivy Bridge pack, is in the 12,1's set; its model recipe is."""
        names = P['generate']({'target': 'mbp121'})
        packs = P['load_packs']()
        owner = {r: pk for pk, d in packs.items() for r in d.get('recipes', [])}
        self.assertIn('opencore-mbp121', names)
        self.assertIn('hao-opencore', names)
        for n in names:
            req = recipe(n)['requires']
            self.assertNotIn('!avx2', req['cpu'] if req['cpu'] != 'any' else [], n)
            self.assertNotIn('intel-ivb', req['gpu'] if req['gpu'] != 'any' else [], n)
            self.assertNotIn(owner.get(n), IVY_PACKS, n)

    def test_plan_meets_every_requires(self):
        names = P['generate']({'target': 'mbp121'})
        self.assertEqual(plan(names, packs=P['detect_packs'](str(TARGET))), names)

    def test_ivy_only_recipes_stop_the_plan(self):
        for name in ('golden-gate-av1-encoder-x86', 'opencore-mbp92', 'metal-metalold', 'golden-gate-hello'):
            with self.assertRaises(ValueError) as e:
                plan([name])
            self.assertIn('requires not met on MacBookPro12,1', str(e.exception))

    def test_metal_3802_extras_cannot_be_named(self):
        # the 12,1's facts: a 12,1 profile must not name metal-3802-extras; its recipes are HD 4000 only
        names = P['generate']({'target': 'mbp121', 'packs': ['metal-3802-extras']})
        with self.assertRaises(ValueError):
            plan(names)
        for f in (TOP / 'pipeline/profiles').glob('*.json'):
            prof = json.loads(f.read_text())
            if prof.get('target') == 'mbp121':
                self.assertFalse(set(prof.get('packs', [])) & set(IVY_PACKS), f.name)

    def test_matrix_plans_it(self):
        cells = [c for c in P['matrix']() if c['target'] == 'mbp121']
        self.assertTrue(cells)
        for c in cells:
            self.assertEqual(c['model'], 'MacBookPro12,1')
            self.assertIn('opencore-mbp121', c['run'] + list(c['refused']))

    def test_reasons_live_in_the_target(self):
        data = json.loads(TARGET.read_text())['platform_data']
        self.assertEqual(P['target_problems'](json.loads(TARGET.read_text())), [])
        self.assertIn('uefi-quirk:EnableVmx', data['opencore_reasons'])
        self.assertIn('kext:AppleHSSPISupport.kext', data['opencore_reasons'])


if __name__ == '__main__':
    unittest.main()
