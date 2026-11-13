import hashlib
import importlib.machinery
import importlib.util
import json
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
loader = importlib.machinery.SourceFileLoader('standalone_kernel', str(ROOT/'kernel-fixes'))
spec = importlib.util.spec_from_loader(loader.name, loader)
kit = importlib.util.module_from_spec(spec)
loader.exec_module(kit)


class Standalone(unittest.TestCase):
    def test_repair_build_and_offline_install_preserve_original_generator_bytes(self):
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch)
            collection = root/'BootKernelExtensions.kc'
            collection.write_bytes(bytes(128)+b'\xcc'*128)
            capture = dict(collection_sha256=hashlib.sha256(collection.read_bytes()).hexdigest(),
                           site_address='0x1000', site_offset='0x0', original=bytes(128).hex(),
                           darwin_version='27.0.0',
                           addresses=dict(virtual_space_start='0x2000', virtual_space_end='0x2008',
                                          pmap_virtual_space='0x2100', reservation='0x2010', map_stolen_data='0x2020'),
                           reservations=dict(reservation=4096))
            source = root/'capture.json'; source.write_text(json.dumps(capture))
            commpage = root/'commpage/IntelCommpage.kext/Contents/MacOS'
            commpage.mkdir(parents=True); (commpage/'IntelCommpage').write_bytes(b'fixture bundle source')
            output = root/'artifact'
            result = subprocess.run([sys.executable, '-I', str(ROOT/'kernel-fixes'), 'build-repairs',
                                     '--capture', str(source), '--boot-kc', str(collection),
                                     '--commpage-artifact', str(root/'commpage'), '--output', str(output)],
                                    cwd=root, text=True, capture_output=True)
            self.assertEqual(0, result.returncode, result.stderr)
            expected, _ = kit.module('original_early_vm', kit.UNIT/'early_vm.py').generate(source, collection)
            actual = plistlib.loads((output/'EFI/OC/config.plist').read_bytes())
            self.assertEqual(expected, actual['Kernel']['Patch'])
            # the record names inputs as the repository does; ones outside it (this scratch folder) by name only
            record = json.loads((output/'build.json').read_text())
            repair = record['inputs']['early_vm_repair']
            self.assertEqual(('capture.json', 'BootKernelExtensions.kc', 'commpage'),
                             (repair['capture_path'], repair['collection_path'], record['inputs']['commpage']))
            self.assertNotIn(str(root.resolve()), (output/'build.json').read_text())
            inside = kit.module('early_vm_paths', kit.UNIT/'early_vm.py')
            self.assertEqual('tools/kernel-foundation/early_vm.py', inside.repo_path(kit.UNIT/'early_vm.py'))
            self.assertEqual('store/artifacts/x', inside.repo_path(kit.REPO/'store/artifacts/x'))
            esp = root/'esp'; (esp/'EFI/OC').mkdir(parents=True)
            original = dict(Kernel=dict(Patch=[dict(Comment='unrelated', Find=b'old')], Add=[]))
            (esp/'EFI/OC/config.plist').write_bytes(plistlib.dumps(original))
            result = subprocess.run([sys.executable, '-I', str(ROOT/'kernel-fixes'), 'install-kernel',
                                     '--esp-root', str(esp), '--boot-kc', str(collection), '--artifact', str(output)],
                                    cwd=root, text=True, capture_output=True)
            self.assertEqual(0, result.returncode, result.stderr)
            installed = plistlib.loads((esp/'EFI/OC/config.plist').read_bytes())
            self.assertEqual(expected, installed['Kernel']['Patch'][1:])
            self.assertEqual(original['Kernel']['Patch'][0], installed['Kernel']['Patch'][0])
            self.assertEqual(b'fixture bundle source', (esp/'EFI/OC/Kexts/IntelCommpage.kext/Contents/MacOS/IntelCommpage').read_bytes())

    def test_cli_recipes_resolve_without_parent_source(self):
        with tempfile.TemporaryDirectory() as scratch:
            for command in ['commpage-kext', 'iosurface-layout', 'sandbox-layout', 'build-repairs', 'install-kernel']:
                result = subprocess.run([sys.executable, '-I', str(ROOT/'kernel-fixes'), command, '--help'],
                                        cwd=scratch, capture_output=True, text=True)
                self.assertEqual(0, result.returncode, result.stderr)

    def test_vendored_source_and_profiles_match_recorded_hashes(self):
        for name in ['Lilu', 'MacKernelSDK']:
            folder = ROOT/'vendor'/name
            metadata = json.loads((folder/'source.json').read_text())
            for relative, expected in metadata['files'].items():
                self.assertEqual(expected, hashlib.sha256((folder/relative).read_bytes()).hexdigest(), relative)
        profile = json.loads((ROOT/'profiles/26A428/profile.json').read_text())
        for name, metadata in profile['definitions'].items():
            self.assertEqual(metadata['sha256'], hashlib.sha256((ROOT/'profiles/26A428'/name).read_bytes()).hexdigest())


if __name__ == '__main__':
    unittest.main()
