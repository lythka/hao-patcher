"""Host tests for kernel-foundation: kernel packaging and VM ESP assembly on small fixtures.

Run: python3.14 -I -m unittest discover -s tools/kernel-foundation/tests
(the vm-esp test needs Python 3.12 or later for tarfile's data filter).
"""
import contextlib
import io
import hashlib
import importlib.util
import shutil
import json
from pathlib import Path
import plistlib
import struct
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

MAKE = Path(__file__).resolve().parents[1]/'make'
APPLE = '7C436110-AB2A-4BBB-A880-FE41995C9F82'


def make(*args):
    return subprocess.run([sys.executable, '-I', str(MAKE), *map(str, args)],
                          capture_output=True, text=True)


def kernel(uuid):
    command = struct.pack('<II', 0x1b, 24)+uuid
    header = struct.pack('<8I', 0xfeedfacf, 0x1000007, 3, 2, 1, len(command), 1, 0)
    return header+command+b'Darwin Kernel Version 27.0.0: test\0'


def kext(root, name, identifier):
    contents = root/f'{name}.kext/Contents'
    (contents/'MacOS').mkdir(parents=True)
    (contents/'MacOS'/name).write_bytes(b'x')
    (contents/'Info.plist').write_bytes(plistlib.dumps(
        {'CFBundleIdentifier': identifier, 'CFBundleExecutable': name}))
    return contents.parent


class Usage(unittest.TestCase):
    def test_no_arguments_prints_usage(self):
        result = make()
        self.assertEqual(result.returncode, 2)
        for command in ('kernel', 'commpage-kext', 'vm-esp'):
            self.assertIn(command, result.stdout)


class Kernel(unittest.TestCase):
    def test_packages_kernel_unchanged_with_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root/'kernel'
            source.write_bytes(kernel(bytes(range(16))))
            result = make('kernel', '--kernel', source, '--out', root/'out')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((root/'out/payload/kernel').read_bytes(), source.read_bytes())
            record = json.loads((root/'out/build.json').read_text())
            self.assertEqual(record['kernel_uuid'], bytes(range(16)).hex())
            self.assertEqual(record['kernel_version'], 'Darwin Kernel Version 27.0.0: test')
            self.assertEqual(record['operation'], 'kernel')

    def test_rejects_non_intel_kernel(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)/'kernel'
            source.write_bytes(struct.pack('<II', 0xfeedfacf, 0x100000c)+b'\0'*24)
            result = make('kernel', '--kernel', source, '--out', Path(directory)/'out')
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('thin x86_64', result.stderr)


@unittest.skipIf(sys.version_info < (3, 12), 'vm-esp needs Python 3.12 or later')
class VmEsp(unittest.TestCase):
    def fixtures(self, root):
        reference = root/'reference/EFI/OC'
        (reference/'Kexts').mkdir(parents=True)
        (reference/'Drivers').mkdir()
        (reference/'config.plist').write_bytes(plistlib.dumps({
            'Kernel': {'Add': [], 'Patch': []}, 'Booter': {'Patch': []},
            'NVRAM': {'Add': {APPLE: {'boot-args': 'keepsyms=1'}}, 'Delete': {}},
            'UEFI': {'Drivers': [], 'APFS': {}}}))
        donor = root/'donor'
        kext(donor/'EFI/OC/Kexts', 'Lilu', 'as.vit9696.Lilu')
        (donor/'EFI/OC/Drivers').mkdir(parents=True)
        (donor/'EFI/OC/Drivers/apfs_aligned.efi').write_bytes(b'efi')
        (donor/'EFI/OC/config.plist').write_bytes(plistlib.dumps({
            'Kernel': {'Add': [dict(BundlePath='Lilu.kext', Enabled=True)]},
            'Booter': {'Patch': []},
            'NVRAM': {'Add': {APPLE: {'boot-args': '-no_compat_check'}}}}))
        with tarfile.open(root/'donor.tar', 'w') as archive:
            archive.add(donor/'EFI', 'EFI')
        return root/'reference', root/'donor.tar'

    def test_without_and_with_extra_kexts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reference, donor = self.fixtures(root)
            base = ['vm-esp', '--reference', reference, '--donor-esp', donor, '--darwin-major', 27]
            result = make(*base, '--out', root/'plain')
            self.assertEqual(result.returncode, 0, result.stderr)
            config = plistlib.loads((root/'plain/EFI/OC/config.plist').read_bytes())
            self.assertEqual([e['BundlePath'] for e in config['Kernel']['Add']], ['Lilu.kext'])
            self.assertIn('-lilubetaall', config['NVRAM']['Add'][APPLE]['boot-args'])

            extra = [kext(root/'extra', 'HaoExec', 'com.apple.kec.HaoExec'),
                     kext(root/'extra', 'Other', 'org.example.Other')]
            result = make(*base, '--kext', extra[0], '--kext', extra[1], '--out', root/'kexts')
            self.assertEqual(result.returncode, 0, result.stderr)
            config = plistlib.loads((root/'kexts/EFI/OC/config.plist').read_bytes())
            added = {e['BundlePath']: e for e in config['Kernel']['Add']}
            self.assertEqual(list(added), ['Lilu.kext', 'HaoExec.kext', 'Other.kext'])
            self.assertEqual(added['HaoExec.kext']['ExecutablePath'], 'Contents/MacOS/HaoExec')
            self.assertEqual(added['HaoExec.kext']['MaxKernel'], '27.99.99')
            self.assertTrue((root/'kexts/EFI/OC/Kexts/Other.kext/Contents/Info.plist').is_file())
            record = json.loads((root/'kexts/build.json').read_text())
            self.assertEqual(len(record['inputs']['kexts']), 2)

    def test_prebuilt_repairs_keep_order_and_require_matching_collection(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reference, donor = self.fixtures(root)
            collection = root/'Boot.kc'; collection.write_bytes(b'fixture collection')
            artifact = root/'fix-artifact'; (artifact/'EFI/OC').mkdir(parents=True)
            patches = [dict(Comment='first repair', Find=b'old', Replace=b'new'),
                       dict(Comment='second repair', Find=b'a', Replace=b'b')]
            repair = dict(capture=dict(collection_sha256=hashlib.sha256(collection.read_bytes()).hexdigest()))
            (artifact/'build.json').write_text(json.dumps(dict(inputs=dict(early_vm_repair=repair))))
            (artifact/'EFI/OC/config.plist').write_bytes(plistlib.dumps(dict(Kernel=dict(Patch=patches))))
            args = ['vm-esp', '--reference', reference, '--donor-esp', donor, '--darwin-major', 27,
                    '--kernel-repair-artifact', artifact, '--boot-kc', collection]
            result = make(*args, '--out', root/'efi')
            self.assertEqual(0, result.returncode, result.stderr)
            config = plistlib.loads((root/'efi/EFI/OC/config.plist').read_bytes())
            self.assertEqual(patches, config['Kernel']['Patch'])
            self.assertEqual(repair, json.loads((root/'efi/build.json').read_text())['inputs']['early_vm_repair'])
            collection.write_bytes(b'another collection')
            result = make(*args, '--out', root/'bad-efi')
            self.assertNotEqual(0, result.returncode)
            self.assertIn('does not match BootKC', result.stderr)


class PackageCalls(unittest.TestCase):
    def workspace(self, root):
        tools = root/'tools/kernel-foundation'; tools.mkdir(parents=True)
        shutil.copy2(MAKE, tools/'make')
        package = root/'tools/kernel-fixes'; package.mkdir(parents=True)
        return tools, package

    def test_generators_use_the_kernel_kit_cli(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); tools, package = self.workspace(root)
            (package/'kernel-fixes').write_text('import json,sys; print(json.dumps(sys.argv[1:]))')
            for command in ('commpage-kext', 'sandbox-layout', 'iosurface-layout', 'build-repairs', 'install-kernel'):
                result = subprocess.run([sys.executable, '-I', str(tools/'make'), command, '--explicit', 'input'],
                                        cwd=directory, capture_output=True, text=True)
                self.assertEqual(0, result.returncode, result.stderr)
                self.assertEqual([command, '--explicit', 'input'], json.loads(result.stdout))

    def test_legacy_capture_loads_the_repository_generator(self):
        spec = importlib.util.spec_from_file_location('vm_esp', MAKE.with_name('vm_esp.py'))
        vm = importlib.util.module_from_spec(spec); spec.loader.exec_module(vm)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); tools, package = self.workspace(root)
            reference, donor = VmEsp().fixtures(root)
            source = tools
            (source/'early_vm.py').write_text(
                "def generate(capture, collection):\n"
                "    return [dict(Comment='selected package fixture')], dict(marker='selected package')\n")
            capture = root/'capture.json'; capture.write_text('{}')
            collection = root/'Boot.kc'; collection.write_bytes(b'fixture')
            argv = ['vm-esp', '--reference', str(reference), '--donor-esp', str(donor), '--darwin-major', '27',
                    '--early-vm-capture', str(capture), '--boot-kc', str(collection), '--out', str(root/'efi')]
            with patch.object(vm, 'ROOT', root), patch.object(sys, 'argv', argv), \
                    patch.object(vm.subprocess, 'check_output', return_value='fixture\n'), \
                    contextlib.redirect_stdout(io.StringIO()):
                vm.main()
            config = plistlib.loads((root/'efi/EFI/OC/config.plist').read_bytes())
            self.assertEqual([dict(Comment='selected package fixture')], config['Kernel']['Patch'])
            record = json.loads((root/'efi/build.json').read_text())
            self.assertEqual(str(package), record['inputs']['early_vm_package'])
            self.assertEqual(dict(marker='selected package'), record['inputs']['early_vm_repair'])




if __name__ == '__main__':
    unittest.main()
