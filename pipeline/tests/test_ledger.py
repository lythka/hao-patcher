"""pipeline/ledger.py: one origin + ordered writes per declared file; chains refused unless expected.
python3.14 -I -B -m unittest discover -s pipeline/tests -k ledger"""
import json
from pathlib import Path
import runpy
import tempfile
import unittest

TOP = Path(__file__).resolve().parents[2]
L = runpy.run_path(str(TOP / 'pipeline' / 'ledger.py'))

OCLP = [('System/Library/Frameworks/Metal.framework', 'Merge System Volume', 'Metal 3802 Common'),
        ('System/Library/PrivateFrameworks/X.framework/Resources/default.metallib', 'Overwrite System Volume',
         'Metal 3802 .metallibs Tahoe')]
TAHOE = ['System/Library/Extensions']
METAL = 'System/Library/Frameworks/Metal.framework/Versions/A/Metal'


class Ledger(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.root = self.d / 'patches'; self.root.mkdir()
        self.target = self.d / 'mbp92.json'; self.target.write_text(json.dumps({'model': 'MacBookPro9,2'}))
        self.order = []

    def recipe(self, name, files, specs=None):
        d = self.root / name; d.mkdir()
        (d / 'patch.json').write_text(json.dumps({'revision': 1, 'stage': 'oclp-hook', 'applies': 'any',
                                                  'files': files}))
        for fname, spec in (specs or {}).items():
            (d / 'edits').mkdir(exist_ok=True)
            (d / 'edits' / fname).write_text(json.dumps(spec))
        self.order.append(name)

    def spec(self, path, kind='oclp', sign=True):
        return {'format': 1, 'path': path, 'origin': {'kind': kind},
                'edits': [{'id': 'e', 'kind': 'literal', 'at': 0, 'find': '01', 'replace': '00', 'reason': 'x'}],
                'sign': {'mode': 'adhoc'} if sign else None}

    def ledger(self):
        s = self.d / 'set.txt'; s.write_text('\n'.join(self.order) + '\n')
        return L['build'](s, self.target, '25G342', root=self.root, layer_data=(OCLP, TAHOE))

    def test_bases(self):
        self.assertEqual(L['base_of'](METAL, OCLP, TAHOE)[0], 'oclp')
        self.assertEqual(L['base_of']('System/Library/Extensions/A.kext/A', OCLP, TAHOE)[0], 'tahoe')
        self.assertEqual(L['base_of']('usr/lib/libz.dylib', OCLP, TAHOE)[0], 'apple')
        self.assertEqual(L['base_of']('EFI/OC/config.plist', OCLP, TAHOE)[0], 'oclp')

    def test_single_writers_are_clean(self):
        self.recipe('a', [{'path': METAL, 'edits': 'edits/m.json'}], {'m.json': self.spec(METAL)})
        self.recipe('b', [{'path': 'usr/lib/new.dylib', 'origin': {'kind': 'hao'}}])
        led = self.ledger()
        self.assertEqual([e['chain'] for e in led.values()], [None, None])
        self.assertEqual(L['lint'](led, {}), [])

    def test_chain_refused_unless_expected(self):
        self.recipe('a', [{'path': METAL, 'edits': 'edits/m.json'}], {'m.json': self.spec(METAL)})
        self.recipe('b', [{'path': METAL, 'edits': 'edits/m.json'}], {'m.json': self.spec(METAL)})
        led = self.ledger()
        self.assertEqual(led[METAL]['chain'], 'chain')
        self.assertEqual(led[METAL]['signs'], 2)
        self.assertIn('chain by oclp -> a -> b', L['lint'](led, {})[0])
        self.assertEqual(L['lint'](led, {METAL: {'chain': 'chain', 'writers': ['oclp', 'a', 'b']}}), [])

    def test_two_origins_and_dead_edits(self):
        self.recipe('a', [{'path': 'usr/lib/x', 'origin': {'kind': 'hao'}}])
        self.recipe('b', [{'path': 'usr/lib/x', 'origin': {'kind': 'hao'}}])
        self.recipe('c', [{'path': METAL, 'edits': 'edits/m.json'}], {'m.json': self.spec(METAL)})
        self.recipe('d', [{'path': METAL, 'origin': {'kind': 'hao'}}])
        led = self.ledger()
        self.assertEqual((led['usr/lib/x']['chain'], led[METAL]['chain']), ('two-origins', 'dead-edits'))

    def test_file_inside_another_recipes_tree(self):
        self.recipe('a', [{'path': 'usr/lib/hao', 'origin': {'kind': 'hao'}, 'tree': True}])
        self.recipe('b', [{'path': 'usr/lib/hao/extra.dylib', 'origin': {'kind': 'hao'}}])
        led = self.ledger()
        self.assertEqual(led['usr/lib/hao/extra.dylib']['chain'], 'inside-tree')
        self.assertEqual(L['writer_names'](led['usr/lib/hao/extra.dylib']), ['apple', 'a (tree usr/lib/hao)', 'b'])

    def test_superseded_oclp_overwrite(self):
        path = OCLP[1][0]
        self.recipe('a', [{'path': path, 'origin': {'kind': 'hao'}}])
        self.assertEqual(self.ledger()[path]['chain'], 'superseded')

    def test_spec_origin_must_match_the_file(self):
        self.recipe('a', [{'path': METAL, 'edits': 'edits/m.json'}], {'m.json': self.spec(METAL, kind='apple')})
        led = self.ledger()
        self.assertIn('spec origin apple, but', led[METAL]['problems'][0])

    def test_stale_expected_entry_refused(self):
        self.recipe('a', [{'path': 'usr/lib/x', 'origin': {'kind': 'hao'}}])
        bad = L['lint'](self.ledger(), {'usr/lib/x': {'chain': 'two-origins', 'writers': ['apple', 'a', 'b']}})
        self.assertIn('no longer a chain: remove it', bad[0])


class Repository(unittest.TestCase):
    def test_shipped_set_has_only_expected_chains(self):
        # a new chain, or a listed one that was collapsed, means: update pipeline/ledger-expected.json on purpose
        setfile = TOP / 'pipeline/sets/golden-gate-26B131.txt'
        led = L['build'](setfile, TOP / 'pipeline/targets/mbp92.json', '25G342')
        self.assertEqual(L['lint'](led, L['expected_for']('golden-gate-26B131')), [])


if __name__ == '__main__':
    unittest.main()
