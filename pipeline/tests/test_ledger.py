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
        # an opt-in (dev) writer may only extend an expected chain at its end
        self.assertEqual(L['lint'](led, {METAL: {'chain': 'chain', 'writers': ['oclp', 'a']}}, dev={'b'}), [])
        self.assertTrue(L['lint'](led, {METAL: {'chain': 'chain', 'writers': ['oclp', 'a']}}, dev=()))
        self.assertTrue(L['lint'](led, {METAL: {'chain': 'chain', 'writers': ['oclp', 'b']}}, dev={'a'}))
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
        self.recipe('b', [{'path': 'usr/lib/y', 'origin': {'kind': 'hao'}}])
        bad = L['lint'](self.ledger(), {'usr/lib/x': {'chain': 'two-origins', 'writers': ['apple', 'a', 'b']}})
        self.assertIn('no longer a chain: remove it', bad[0])

    def test_expected_chains_are_shared_by_every_set_with_those_recipes(self):
        # the same chain in two sets passes with one entry; a set without one of its recipes is not held to it
        self.recipe('a', [{'path': METAL, 'edits': 'edits/m.json'}], {'m.json': self.spec(METAL)})
        self.recipe('b', [{'path': METAL, 'edits': 'edits/m.json'}], {'m.json': self.spec(METAL)})
        self.recipe('dev', [{'path': 'usr/lib/dev', 'origin': {'kind': 'hao'}}])
        expected = {METAL: {'chain': 'chain', 'writers': ['oclp', 'a', 'b']}}
        self.assertEqual(L['lint'](self.ledger(), expected), [])                    # a, b, dev
        self.order.remove('dev')
        self.assertEqual(L['lint'](self.ledger(), expected), [])                    # a, b
        self.order.remove('b')
        self.assertEqual(L['lint'](self.ledger(), expected), [])                    # a alone: entry not about it

    def test_write_expected_keeps_other_sets_entries(self):
        self.recipe('a', [{'path': METAL, 'edits': 'edits/m.json'}], {'m.json': self.spec(METAL)})
        self.recipe('b', [{'path': METAL, 'edits': 'edits/m.json'}], {'m.json': self.spec(METAL)})
        out = self.d / 'expected.json'
        other = {'usr/lib/z': {'chain': 'two-origins', 'writers': ['apple', 'c', 'd']}}
        out.write_text(json.dumps({'chains': dict(other, **{'usr/lib/gone': {'chain': 'chain',
                                                                            'writers': ['apple', 'a', 'b']}})}))
        self.assertEqual(L['write_expected'](self.ledger(), out), 1)
        self.assertEqual(json.loads(out.read_text())['chains'],
                         dict(other, **{METAL: {'chain': 'chain', 'writers': ['oclp', 'a', 'b']}}))

    def test_a_recipe_editing_a_file_of_its_own_tree(self):
        kext = 'EFI/OC/Kexts/HaoExec.kext'
        self.recipe('oc', [{'path': kext, 'origin': {'kind': 'hao', 'row': 'r'}, 'tree': True},
                           {'path': kext + '/Contents/MacOS/HaoExec', 'edits': 'edits/s.json'}],
                    {'s.json': {'format': 1, 'path': kext + '/Contents/MacOS/HaoExec', 'origin': {'kind': 'hao'},
                                'edits': [{'id': 's', 'kind': 'op', 'op': 'hao.stamp', 'args': {}, 'reason': 'x'}]}})
        led = self.ledger()
        e = led[kext + '/Contents/MacOS/HaoExec']
        self.assertEqual((e['chain'], e['problems'], e['own_tree']['tree']), (None, [], kext))


# The files the 26B131 baseline image (dev-bs-test) holds with the `restricted` flag, among the declared ones: each
# recipe's hook runs `chflags restricted` (or keeps the flag of the file it replaces).
RESTRICTED = [
    'System/Library/Extensions/AppleIntelHD4000GraphicsMTLDriver.bundle/Contents/MacOS/AppleIntelHD4000GraphicsMTLDriver',
    'System/Library/Frameworks/CoreServices.framework/Versions/A/Frameworks/Metadata.framework/Versions/A/Support/corespotlightd',
    'System/Library/Frameworks/CoreServices.framework/Versions/A/Frameworks/Metadata.framework/Versions/A/Support/managedcorespotlightd',
    'System/Library/Frameworks/Metal.framework/Versions/A/Metal',
    'System/Library/Frameworks/Metal.framework/Versions/A/XPCServices/MTLCompilerService.xpc/Contents/MacOS/MTLCompilerService',
    'System/Library/LaunchAgents/com.apple.hybridsearchd.plist',
    'System/Library/PrivateFrameworks/CoreWiFi.framework/Versions/A/CoreWiFi',
    'System/Library/PrivateFrameworks/CoreWiFi.framework/Versions/A/CoreWiFiTahoe.dylib',
    'System/Library/PrivateFrameworks/IO80211.framework/Versions/A/IO80211',
    'System/Library/PrivateFrameworks/IO80211.framework/Versions/A/IO80211OCLP.dylib',
    'System/Library/PrivateFrameworks/MTLCompiler.framework/Versions/3802/Libraries/libhaoMetalCompilerCompat.dylib',
    'System/Library/PrivateFrameworks/SetupAssistantSupportUI.framework/Versions/A/Resources/default.metallib',
    'System/Library/PrivateFrameworks/WiFiPeerToPeer.framework/Versions/A/WiFiPeerToPeer',
    'System/Library/PrivateFrameworks/WiFiPeerToPeer.framework/Versions/A/WiFiPeerToPeerOCLP.dylib',
    'System/Library/xpc/launchd.plist',
    'usr/lib/corespotlight_compat.dylib', 'usr/lib/dss_shim.dylib', 'usr/lib/dyld', 'usr/lib/libhelloblur.dylib',
    'usr/lib/libmetal-framebuffer.dylib', 'usr/lib/libmetalold-selectors.dylib', 'usr/lib/windowserver_filter.dylib',
    'usr/libexec/hao']


class Repository(unittest.TestCase):
    def test_install_attributes_the_baseline_image_has_are_declared(self):
        led = L['build'](TOP / 'pipeline/sets/golden-gate-26B131.txt', TOP / 'pipeline/targets/mbp92.json', '25G342')
        install = lambda p: next((w['install'] for w in reversed(led[p]['writers']) if w.get('install')), {})
        self.assertEqual(len(RESTRICTED), 23)
        for p in RESTRICTED:
            self.assertEqual(install(p).get('flags'), ['restricted'], p)
        self.assertEqual(install('System/Library/Kernels/kernel')['mode'], '0644')     # golden-pristine.sh -m 644
        exe = led['EFI/OC/Kexts/HaoExec.kext/Contents/MacOS/HaoExec']
        self.assertEqual((exe['writers'][-1]['spec'], exe['own_tree']['tree'], exe['chain']),
                         ('edits/haoexec-identity.json', 'EFI/OC/Kexts/HaoExec.kext', None))
        self.assertEqual(led['usr/lib/hao/native-identities']['writers'][-1]['after'], ['overlays'])

    def test_shipped_sets_have_only_expected_chains(self):
        # a new chain, or a listed one that was collapsed, means: update pipeline/ledger-expected.json on purpose.
        # dev-bs-test (golden-gate-26B131 + pack dev) has the same chains, dev-diagnostics appended to config.plist's
        for name in ('golden-gate-26B131', 'dev-bs-test'):
            setfile = TOP / 'pipeline/sets' / (name + '.txt')
            led = L['build'](setfile, TOP / 'pipeline/targets/mbp92.json', '25G342')
            self.assertEqual(L['lint'](led, L['expected_for'](name)), [], name)


if __name__ == '__main__':
    unittest.main()
