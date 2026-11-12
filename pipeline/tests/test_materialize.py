"""pipeline/materialize.py: each declared file made once into pack overlays that hao patch applies.
python3.14 -I -B -m unittest discover -s pipeline/tests -k materialize"""
import io
import json
from pathlib import Path
import runpy
import sys
import tarfile
import tempfile
import unittest

TOP = Path(__file__).resolve().parents[2]
M = runpy.run_path(str(TOP / 'pipeline' / 'materialize.py'))
L = runpy.run_path(str(TOP / 'pipeline' / 'ledger.py'))
sys.path.insert(0, str(TOP / 'tools' / 'hao' / 'src'))
import livepatch   # noqa: E402

METAL = 'System/Library/Frameworks/Metal.framework/Versions/A/Metal'
OCLP = [('System/Library/Frameworks/Metal.framework', 'Merge System Volume', 'Metal 3802 Common')]


def literal(eid, at, find, replace, kind='oclp', sign=None):
    return {'format': 1, 'path': METAL, 'origin': {'kind': kind},
            'edits': [{'id': eid, 'kind': 'literal', 'at': at, 'find': find, 'replace': replace, 'reason': 'x'}],
            'sign': sign}


class Materialize(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.root = self.d / 'patches'; self.root.mkdir()
        self.order = []
        # OCLP payload with Metal at its support-package path; a Golden Gate system root
        oclp = self.d / 'oclp' / 'payload' / '13.2.1-25' / 'System/Library/Frameworks/Metal.framework/Versions/A'
        oclp.mkdir(parents=True)
        (oclp / 'Metal').write_bytes(b'\x01\x02\x03\x04oclp-metal')
        system = self.d / 'system' / 'usr/lib'; system.mkdir(parents=True)
        (system / 'libz.dylib').write_bytes(b'golden gate libz')
        # rows: a file, a folder laid out differently from the install path, a folder with a tar
        rows = self.d / 'rows'; (rows / 'framework' / 'runtime').mkdir(parents=True)
        (rows / 'one.dylib').write_bytes(b'\xcf\xfa\xed\xfe one')
        (rows / 'framework' / 'runtime' / 'Lib.dylib').write_bytes(b'lib in a folder')
        (rows / 'caches').mkdir()
        with tarfile.open(rows / 'caches' / 'data.tar', 'w') as t:
            data = b'cache bytes'
            info = tarfile.TarInfo('private/var/c/store.index'); info.size = len(data)
            t.addfile(info, io.BytesIO(data))
        self.sources = M['Sources'](
            rows={'one': str(rows / 'one.dylib'), 'framework': str(rows / 'framework'), 'caches': str(rows / 'caches')},
            oclp_payload=str(self.d / 'oclp' / 'payload'),
            oclp_map=[('System/Library/Frameworks/Metal.framework', 'Merge System Volume',
                       '13.2.1-25/System/Library/Frameworks/Metal.framework')],
            system=str(self.d / 'system'))

    def recipe(self, name, files, specs=None):
        d = self.root / name; d.mkdir()
        (d / 'patch.json').write_text(json.dumps({'revision': 1, 'stage': 'oclp-hook', 'applies': 'any', 'files': files}))
        for fname, spec in (specs or {}).items():
            (d / 'edits').mkdir(exist_ok=True)
            (d / 'edits' / fname).write_text(json.dumps(spec))
        self.order.append(name)

    def run_all(self, packs=None):
        s = self.d / 'set.txt'; s.write_text('\n'.join(self.order) + '\n')
        t = self.d / 'mbp92.json'; t.write_text(json.dumps({'model': 'MacBookPro9,2'}))
        led = L['build'](s, t, '25G342', root=self.root, layer_data=(OCLP, []))
        packs = packs or {}
        out = self.d / 'out'
        report = M['materialize'](led, self.root, self.sources, out, self.d / 'store',
                                  lambda r: packs.get(r, 'base'), lambda p: 'live-capable', repo=self.d)
        return {r['path']: r for r in report}, out

    def test_sources_edits_chain_and_manifest(self):
        self.recipe('hello', [{'path': METAL, 'edits': 'edits/m.json'}], {'m.json': literal('one', '0x0', '01', 'AA')})
        self.recipe('more', [{'path': METAL, 'edits': 'edits/m.json'}], {'m.json': literal('two', '0x1', '02', 'BB')})
        self.recipe('libs', [
            {'path': 'usr/lib/one.dylib', 'origin': {'kind': 'hao', 'row': 'one'}},
            {'path': 'usr/lib/rt/Lib.dylib', 'origin': {'kind': 'hao', 'row': 'framework'},
             'source': 'runtime/Lib.dylib', 'install': {'mode': '0755', 'owner': 'root:wheel', 'flags': ['restricted']}},
            {'path': 'private/var/c/store.index', 'origin': {'kind': 'hao', 'row': 'caches'},
             'source': 'data.tar!private/var/c/store.index'},
            {'path': 'usr/lib/generated', 'origin': {'kind': 'hao'}},
            {'path': 'usr/lib/missing.dylib', 'origin': {'kind': 'hao', 'row': 'framework'}}])
        report, out = self.run_all()
        self.assertEqual((out / 'base/files' / METAL).read_bytes(), b'\xaa\xbb\x03\x04oclp-metal')   # chain, in order
        self.assertEqual((out / 'base/files/usr/lib/rt/Lib.dylib').read_bytes(), b'lib in a folder')
        self.assertEqual((out / 'base/files/private/var/c/store.index').read_bytes(), b'cache bytes')
        self.assertEqual(report['usr/lib/generated']['outcome'], 'deferred')
        self.assertEqual(report['usr/lib/missing.dylib']['outcome'], 'unresolved')
        self.assertIn('declare "source"', report['usr/lib/missing.dylib']['reason'])
        files = json.loads((out / 'base/manifest.json').read_text())['files']
        self.assertEqual(files['usr/lib/rt/Lib.dylib']['flags'], ['restricted'])
        self.assertEqual(files['usr/lib/one.dylib']['mode'], '0755')          # a Mach-O without install attributes
        self.assertEqual(files['private/var/c/store.index']['mode'], '0644')

    def test_system_base_external_ops_and_efi(self):
        self.recipe('sys', [{'path': 'usr/lib/libz.dylib', 'edits': 'edits/z.json'}],
                    {'z.json': {'format': 1, 'path': 'usr/lib/libz.dylib', 'origin': {'kind': 'apple'},
                                'edits': [{'id': 'z', 'kind': 'literal', 'at': 0, 'find': '67', 'replace': '47',
                                           'reason': 'x'}]}})
        self.recipe('kc', [{'path': 'System/Library/KernelCollections/BootKernelExtensions.kc', 'edits': 'edits/k.json'}],
                    {'k.json': {'format': 1, 'path': 'System/Library/KernelCollections/BootKernelExtensions.kc',
                                'origin': {'kind': 'hao'}, 'edits': [{'id': 'link', 'kind': 'op', 'op': 'kc.link',
                                                                      'args': {}, 'reason': 'x'}]}})
        report, out = self.run_all()
        self.assertEqual((out / 'base/files/usr/lib/libz.dylib').read_bytes(), b'Golden gate libz')
        self.assertIn('kc.link', report['System/Library/KernelCollections/BootKernelExtensions.kc']['reason'])

    def test_store_reuse_and_hao_patch_applies_the_overlay(self):
        self.recipe('hello', [{'path': METAL, 'edits': 'edits/m.json'}], {'m.json': literal('one', '0x0', '01', 'AA')})
        self.run_all()
        results = list((self.d / 'store' / 'results').glob('*.json'))
        self.run_all()
        self.assertEqual(list((self.d / 'store' / 'results').glob('*.json')), results)   # reused, nothing new
        target = self.d / 'target'
        (target / 'System/Library/Frameworks/Metal.framework/Versions/A').mkdir(parents=True)
        (target / METAL).write_bytes(b'\x01\x02\x03\x04oclp-metal')
        overlay = livepatch.load_overlay(self.d / 'out' / 'base')
        rec = livepatch.apply([overlay], target, self.d / 'state')
        self.assertEqual((target / METAL).read_bytes(), b'\xaa\x02\x03\x04oclp-metal')
        self.assertEqual([f['path'] for f in rec['files']], [METAL])

    def test_last_writer_decides_the_pack(self):
        self.recipe('a', [{'path': 'usr/lib/one.dylib', 'origin': {'kind': 'hao', 'row': 'one'}}])
        report, out = self.run_all(packs={'a': 'metal-3802'})
        self.assertTrue((out / 'metal-3802/files/usr/lib/one.dylib').is_file())
        self.assertEqual(json.loads((out / 'metal-3802/manifest.json').read_text())['phase'], 'live-capable')


if __name__ == '__main__':
    unittest.main()
