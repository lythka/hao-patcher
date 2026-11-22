"""pipeline/candidates: the registry holds only public facts, and the report says what a release would refuse.

The registry is a question, not a target: a row that carries a target's measured fields (board id, CPU flags, PCI,
ioreg, ESP) is refused. The report reads the real release profiles and sets, so these tests pin the shape and the
two decisions that matter: an Ivy Bridge candidate meets the HD 4000 path, and a Haswell one does not.
"""
import json
from pathlib import Path
import runpy
import subprocess
import sys
import unittest

TOP = Path(__file__).resolve().parents[2]
C = runpy.run_path(str(TOP / 'pipeline' / 'candidates'))
REG = C['read_registry']()
DOC = TOP / 'docs' / 'candidates.md'
START, END = '<!-- candidates-table:start -->', '<!-- candidates-table:end -->'


def release_names():
    """Every profile with "release": true, in the order the report uses."""
    out = []
    for f in sorted((TOP / 'pipeline' / 'profiles').glob('*.json')):
        if json.loads(f.read_text()).get('release') is True:
            out.append(f.stem)
    return out


RELEASES = release_names()


def clone(reg):
    return json.loads(json.dumps(reg))


class Registry(unittest.TestCase):
    def test_registry_follows_the_format(self):
        self.assertEqual([], C['problems'](REG))

    def test_a_target_field_is_refused(self):
        bad = clone(REG)
        bad['candidates'][0]['pci'] = []
        self.assertTrue(any('belongs to a target' in b for b in C['problems'](bad)), C['problems'](bad))

    def test_an_unknown_gpu_family_is_refused(self):
        bad = clone(REG)
        bad['candidates'][0]['gpu'] = ['intel-cfl']
        self.assertTrue(any('not in patches.GPUS' in b for b in C['problems'](bad)), C['problems'](bad))

    def test_models_are_unique(self):
        models = [c['model'] for c in REG['candidates'] if c['model']]
        self.assertEqual(len(models), len(set(models)))

    def test_a_request_names_its_issue(self):
        for c in REG['candidates']:
            if c['status'] in ('requested', 'software'):
                self.assertIsInstance(c['issue'], int, c['id'])
            else:
                self.assertIsNone(c['issue'], c['id'])

    def test_the_cli_check_passes(self):
        r = subprocess.run([sys.executable, '-I', str(TOP / 'pipeline' / 'candidates'), '--check'],
                           capture_output=True, text=True)
        self.assertEqual(0, r.returncode, r.stdout + r.stderr)


class Report(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rep = C['report'](REG)

    def cells(self, model):
        row = next(c for c in self.rep['candidates'] if c['model'] == model)
        return row['cells']

    def cell(self, model, profile):
        return next(c for c in self.cells(model) if c['profile'] == profile)

    def test_every_release_profile_is_reported(self):
        self.assertEqual(RELEASES, self.rep['profiles'])
        for c in self.rep['candidates']:
            self.assertEqual(set(RELEASES), {cell['profile'] for cell in c['cells']}, c['id'])

    def test_every_cell_has_a_build_pair(self):
        for c in self.rep['candidates']:
            for cell in c['cells']:
                self.assertIsNone(cell['error'], (c['id'], cell['profile']))

    def test_ivy_bridge_candidate_refuses_nothing(self):
        for cell in self.cells('MacBookPro10,1'):
            self.assertEqual({}, cell['refused'], cell['profile'])

    def test_haswell_candidate_refuses_ivy_bridge_recipes(self):
        cell = self.cell('MacBookPro11,2', 'alpha-v0.1.0')
        self.assertIn('golden-gate-hello', cell['refused'])
        self.assertIn('gpu intel-hsw is not one of intel-ivb', ' '.join(cell['refused']['golden-gate-hello']))

    def test_navi_candidate_refuses_the_hd4000_path_and_t2(self):
        cell = self.cell('MacBookPro16,1', 'alpha-v0.1.0')
        self.assertIn('golden-gate-metal-libraries', cell['refused'])
        self.assertIn('opencore-mbp92', cell['refused'])
        self.assertIn('firmware t2 is not one of none', ' '.join(cell['refused']['opencore-mbp92']))

    def test_unknown_is_only_cpu(self):
        for c in self.rep['candidates']:
            for cell in c['cells']:
                for name, why in cell['unknown'].items():
                    self.assertTrue(all(w.startswith('cpu ') for w in why), (name, why))

    def test_a_profile_can_be_chosen(self):
        rep = C['report'](REG, ['generic'])
        self.assertEqual(['generic'], rep['profiles'])
        self.assertEqual([], rep['unknown_profiles'])

    def test_an_unknown_profile_is_named(self):
        rep = C['report'](REG, ['no-such-profile'])
        self.assertEqual(['no-such-profile'], rep['unknown_profiles'])
        self.assertEqual([], rep['profiles'])


class Markdown(unittest.TestCase):
    def test_the_documented_table_is_current(self):
        text = DOC.read_text()
        start = text.index(START) + len(START)
        end = text.index(END)
        self.assertEqual(C['markdown'](REG), text[start:end].strip() + '\n',
                         'docs/candidates.md: regenerate the table with pipeline/candidates --markdown')


if __name__ == '__main__':
    unittest.main()
