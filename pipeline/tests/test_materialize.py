"""pipeline/materialize.py: each declared file made once into pack overlays that hao patch applies.
python3.14 -I -B -m unittest discover -s pipeline/tests -k materialize"""
import hashlib
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
        (self.d / 'system/usr/lib/libold.dylib').write_bytes(b'old library')
        self.recipe('whole', [{'path': 'usr/lib/libold.dylib', 'origin': {'kind': 'hao', 'row': 'one'}},
                              {'path': 'usr/lib/one.dylib', 'origin': {'kind': 'hao', 'row': 'one'}}])
        report, out = self.run_all()
        self.assertEqual((out / 'base/files/usr/lib/libz.dylib').read_bytes(), b'Golden gate libz')
        self.assertIn('kc.link', report['System/Library/KernelCollections/BootKernelExtensions.kc']['reason'])
        # what an image build installing the overlays checks it finds first (pipeline/overlays.py)
        sha = lambda b: hashlib.sha256(b).hexdigest()
        z = report['usr/lib/libz.dylib']
        self.assertEqual((z['writers'], z['paths'], z['recipe']), (['sys'], ['usr/lib/libz.dylib'], 'sys'))
        self.assertEqual(z['base'], {'path': 'usr/lib/libz.dylib', 'sha256': sha(b'golden gate libz'), 'edited': True})
        self.assertEqual(report['usr/lib/libold.dylib']['base'],
                         {'path': 'usr/lib/libold.dylib', 'sha256': sha(b'old library'), 'edited': False})
        self.assertNotIn('base', report['usr/lib/one.dylib'])                 # a new file

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

    def test_a_tree_is_its_own_folder_never_the_whole_row(self):
        kext = self.d / 'rows' / 'payload' / 'HaoExec.kext' / 'Contents'; kext.mkdir(parents=True)
        (kext / 'Info.plist').write_bytes(b'plist')
        (self.d / 'rows' / 'payload' / 'big.aot').write_bytes(b'not part of the kext')
        self.sources.rows['payload'] = str(self.d / 'rows' / 'payload')
        self.recipe('oc', [{'path': 'EFI/OC/Kexts/HaoExec.kext', 'origin': {'kind': 'hao', 'row': 'payload'}, 'tree': True},
                           {'path': 'EFI/OC/Kexts/Missing.kext', 'origin': {'kind': 'hao', 'row': 'payload'}, 'tree': True}])
        report, out = self.run_all()
        made = sorted(str(p.relative_to(out / 'base/files')) for p in (out / 'base/files').rglob('*') if p.is_file())
        self.assertEqual(made, ['EFI/OC/Kexts/HaoExec.kext/Contents/Info.plist'])
        self.assertEqual(report['EFI/OC/Kexts/Missing.kext']['outcome'], 'unresolved')

    def test_hello_metal_is_signed_once_like_its_hook(self):
        # the real hello spec on OCLP's real wrapper, when HELLO_OCLP_METAL is set: the overlay's Metal is
        # byte-identical to edit-metal.py + `codesign -f -s - -i Metal` (what the hook installs)
        import os, shutil, subprocess
        real = os.environ.get('HELLO_OCLP_METAL')
        if not real or not shutil.which('codesign'):
            self.skipTest('set HELLO_OCLP_METAL to OCLP\'s Metal wrapper')
        payload = self.d / 'oclp' / 'payload' / '13.2.1-25' / 'System/Library/Frameworks/Metal.framework/Versions/A'
        shutil.copy(real, payload / 'Metal')
        d = self.root / 'golden-gate-hello'; shutil.copytree(TOP / 'patches/golden-gate-hello/edits', d / 'edits')
        (d / 'patch.json').write_text(json.dumps({'revision': 1, 'stage': 'oclp-hook', 'applies': 'any',
                                                  'files': [{'path': METAL, 'edits': 'edits/metal.json'}]}))
        self.order.append('golden-gate-hello')
        self.run_all()
        ref = self.d / 'ref'
        subprocess.run([sys.executable, '-I', str(TOP / 'patches/golden-gate-hello/src/edit-metal.py'), real, str(ref)],
                       check=True, capture_output=True)
        subprocess.run(['codesign', '-f', '-s', '-', '-i', 'Metal', str(ref)], check=True, capture_output=True)
        self.assertEqual((self.d / 'out/base/files' / METAL).read_bytes(), ref.read_bytes())

    def test_tar_members_keep_their_mode_owner_and_folders(self):
        # as `tar -xp --numeric-owner`: the member's mode and numeric owner, and the tar's folders above it
        with tarfile.open(self.d / 'rows' / 'caches' / 'setup.tar', 'w') as t:
            for name, mode, uid in (('private', 0o755, 0), ('private/var/zz', 0o711, 0), ('private/var/zz/C', 0o700, 248)):
                info = tarfile.TarInfo(name); info.type = tarfile.DIRTYPE; info.mode = mode; info.uid = info.gid = uid
                t.addfile(info)
            data = b'index'
            info = tarfile.TarInfo('private/var/zz/C/store.index'); info.size = len(data)
            info.mode, info.uid, info.gid, info.mtime = 0o600, 248, 248, 1700000000
            t.addfile(info, io.BytesIO(data))
        self.recipe('caches', [
            {'path': 'private/var/zz/C/store.index', 'origin': {'kind': 'hao', 'row': 'caches'},
             'source': 'setup.tar!private/var/zz/C/store.index'},
            {'path': 'private/var/c/store.index', 'origin': {'kind': 'hao', 'row': 'caches'},
             'source': 'data.tar!private/var/c/store.index', 'install': {'mode': '0640', 'owner': 'root:admin'}}])
        report, out = self.run_all()
        m = json.loads((out / 'base/manifest.json').read_text())
        self.assertEqual({k: m['files']['private/var/zz/C/store.index'][k] for k in ('mode', 'owner', 'flags')},
                         {'mode': '0600', 'owner': '248:248', 'flags': []})
        self.assertEqual(m['dirs'], {'private': {'mode': '0755', 'owner': '0:0'},
                                     'private/var/zz': {'mode': '0711', 'owner': '0:0'},
                                     'private/var/zz/C': {'mode': '0700', 'owner': '248:248'}})
        self.assertEqual(m['files']['private/var/zz/C/store.index']['mtime'], 1700000000)   # tar -x keeps it
        self.assertNotIn('mtime', m['files']['private/var/c/store.index'])           # installed with `install`
        self.assertEqual(m['files']['private/var/c/store.index']['mode'], '0640')       # a declaration wins
        self.assertEqual(m['files']['private/var/c/store.index']['owner'], 'root:admin')
        # hao patch puts them in place with those attributes (owners recorded: the test does not run as root)
        calls = []
        attrs = livepatch.Attributes(self.d / 'target', privileged=True,
                                     chown=lambda p, u, g: calls.append((str(p.relative_to(self.d / 'target')), u, g)),
                                     chflags=lambda p, bits: None)
        (self.d / 'target').mkdir()
        livepatch.apply([livepatch.load_overlay(out / 'base')], self.d / 'target', self.d / 'state', attributes=attrs)
        self.assertIn(('private/var/zz/C/store.index', 248, 248), calls)
        self.assertIn(('private/var/zz/C', 248, 248), calls)
        self.assertEqual(oct((self.d / 'target/private/var/zz/C').stat().st_mode & 0o777), '0o700')

    def test_a_stamp_inside_its_own_tree_is_deferred_after_the_kernel_collection(self):
        kext = self.d / 'rows' / 'payload' / 'HaoExec.kext' / 'Contents'
        (kext / 'MacOS').mkdir(parents=True)
        (kext / 'Info.plist').write_bytes(b'plist')
        (kext / 'MacOS' / 'HaoExec').write_bytes(b'\xcf\xfa\xed\xfe unstamped')
        self.sources.rows['payload'] = str(self.d / 'rows' / 'payload')
        kc = 'System/Library/KernelCollections/BootKernelExtensions.kc'
        exe = 'EFI/OC/Kexts/HaoExec.kext/Contents/MacOS/HaoExec'
        self.recipe('kernel', [{'path': kc, 'edits': 'edits/k.json'}],
                    {'k.json': {'format': 1, 'path': kc, 'origin': {'kind': 'hao'},
                                'edits': [{'id': 'link', 'kind': 'op', 'op': 'kc.link', 'args': {}, 'reason': 'x'}]}})
        self.recipe('oc', [{'path': 'EFI/OC/Kexts/HaoExec.kext', 'origin': {'kind': 'hao', 'row': 'payload'}, 'tree': True},
                           {'path': exe, 'edits': 'edits/s.json'}],
                    {'s.json': {'format': 1, 'path': exe, 'origin': {'kind': 'hao', 'row': 'payload'},
                                'edits': [{'id': 'stamp', 'kind': 'op', 'op': 'hao.stamp', 'reason': 'x',
                                           'args': {'kernel_collection': kc}}]}})
        self.recipe('ids', [{'path': 'usr/lib/hao/native-identities', 'origin': {'kind': 'hao'}, 'after': ['overlays']}])
        report, out = self.run_all()
        made = sorted(str(p.relative_to(out / 'base/files')) for p in (out / 'base/files').rglob('*') if p.is_file())
        self.assertEqual(made, ['EFI/OC/Kexts/HaoExec.kext/Contents/Info.plist'])        # never the unstamped copy
        self.assertEqual(report['EFI/OC/Kexts/HaoExec.kext']['declared_separately'], [exe])
        self.assertEqual((report[exe]['outcome'], report[exe]['after']), ('deferred', [kc]))
        self.assertIn('hao.stamp', report[exe]['reason'])
        order = [d['path'] for d in json.loads((out / 'deferred.json').read_text())]
        self.assertLess(order.index(kc), order.index(exe))
        self.assertEqual(order[-1], 'usr/lib/hao/native-identities')                    # after everything else
        self.assertEqual(json.loads((out / 'deferred.json').read_text())[order.index('usr/lib/hao/native-identities')]
                         ['after'], ['overlays'])

    def test_a_signature_takes_another_files_entitlements(self):
        import plistlib, shutil, subprocess
        if sys.platform != 'darwin' or not shutil.which('codesign'):
            self.skipTest('needs macOS codesign')
        sys.path.insert(0, str(TOP / 'tools/edit-engine/src'))
        import signing
        ents = self.d / 'ents.plist'; ents.write_bytes(plistlib.dumps({'com.apple.private.example': True}))
        path = 'usr/libexec/server'
        golden = self.d / 'system' / path; golden.parent.mkdir(parents=True)
        shutil.copyfile('/usr/bin/true', golden)
        subprocess.run(['codesign', '-f', '-s', '-', '-i', 'com.example.server', '--entitlements', str(ents), str(golden)],
                       check=True, capture_output=True)
        tahoe = self.d / 'tahoe'; (tahoe / 'usr/libexec').mkdir(parents=True)
        shutil.copyfile('/usr/bin/true', tahoe / path)
        self.sources.tahoe_roots = [str(tahoe)]
        start = signing.slices((tahoe / path).read_bytes())[0][1]
        byte = (tahoe / path).read_bytes()[start + 8]
        spec = {'format': 1, 'path': path, 'origin': {'kind': 'tahoe'},
                'edits': [{'id': 'x', 'kind': 'literal', 'at': start + 8, 'find': '%02X' % byte,
                           'replace': '%02X' % byte, 'mask': '00', 'reason': 'x'}],
                'sign': {'mode': 'adhoc', 'identifier': 'com.example.server',
                         'entitlements_from': {'kind': 'apple', 'path': path}}}
        self.recipe('serve', [{'path': path, 'input': {'kind': 'tahoe', 'path': path}, 'edits': 'edits/s.json'}],
                    {'s.json': spec})
        report, out = self.run_all()
        self.assertEqual(report[path]['outcome'], 'made', report[path])
        made = (out / 'base/files' / path).read_bytes()
        reference = self.d / 'reference'                # what the hook does: codesign with the golden file's entitlements
        shutil.copyfile(tahoe / path, reference)
        xml = self.d / 'golden.plist'
        xml.write_bytes(subprocess.run(['codesign', '-d', '--entitlements', '-', '--xml', str(golden)],
                                       check=True, capture_output=True).stdout)
        subprocess.run(['codesign', '-f', '-s', '-', '-i', 'com.example.server', '--entitlements', str(xml),
                        str(reference)], check=True, capture_output=True)
        self.assertEqual(signing.compare(reference.read_bytes(), made)['problems'], [])
        unsigned = self.d / 'plain'; shutil.copyfile(tahoe / path, unsigned)
        subprocess.run(['codesign', '-f', '-s', '-', '-i', 'com.example.server', str(unsigned)], check=True,
                       capture_output=True)
        self.assertTrue(signing.compare(reference.read_bytes(), unsigned.read_bytes())['problems'])   # the old gap

    def test_last_writer_decides_the_pack(self):
        self.recipe('a', [{'path': 'usr/lib/one.dylib', 'origin': {'kind': 'hao', 'row': 'one'}}])
        report, out = self.run_all(packs={'a': 'metal-3802'})
        self.assertTrue((out / 'metal-3802/files/usr/lib/one.dylib').is_file())
        self.assertEqual(json.loads((out / 'metal-3802/manifest.json').read_text())['phase'], 'live-capable')


if __name__ == '__main__':
    unittest.main()
