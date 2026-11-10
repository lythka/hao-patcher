import importlib.util
from pathlib import Path
import plistlib
import struct
import tempfile
import unittest


spec = importlib.util.spec_from_file_location('early_kexts', Path(__file__).parents[1]/'src/early_kexts.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class Dependencies(unittest.TestCase):
    def test_system_collection_dependency_is_not_injected_again(self):
        def collection(path, identifiers):
            commands = []
            for identifier in identifiers:
                name = identifier.encode()+b'\0'
                size = (32+len(name)+7) & ~7
                commands.append(struct.pack('<IIQQII', 0x80000035, size, 0, 0, 32, 0)
                                +name+b'\0'*(size-32-len(name)))
            path.write_bytes(struct.pack('<8I', 0xfeedfacf, 0x1000007, 3, 0xc,
                                         len(commands), sum(map(len, commands)), 0, 0)
                             +b''.join(commands))

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            system, esp = root/'system', root/'esp'
            collections = system/'System/Library/KernelCollections'
            collections.mkdir(parents=True)
            collection(collections/'BootKernelExtensions.kc', ['ethernet'])
            collection(collections/'SystemKernelExtensions.kc', ['graphics'])
            bundle = system/'System/Library/Extensions/Display.kext/Contents'
            (bundle/'MacOS').mkdir(parents=True)
            (bundle/'Info.plist').write_bytes(plistlib.dumps(dict(
                CFBundleIdentifier='display', CFBundleExecutable='Display',
                CFBundleVersion='1.0', OSBundleLibraries={'graphics': '1.0'})))
            (bundle/'MacOS/Display').write_bytes(b'display executable')
            (esp/'EFI/OC/Kexts').mkdir(parents=True)
            config = esp/'EFI/OC/config.plist'
            config.write_bytes(plistlib.dumps({'Kernel': {'Add': []}}))
            roots = root/'roots.txt'
            roots.write_text('display\ngraphics\nethernet\n')
            module.apply(system, esp, roots)
            self.assertEqual([entry['BundlePath'] for entry in
                              plistlib.loads(config.read_bytes())['Kernel']['Add']],
                             ['Display.kext'])
            self.assertEqual(sorted(p.name for p in (esp/'EFI/OC/Kexts').iterdir()),
                             ['Display.kext'])

    def test_orders_dependencies_and_keeps_already_linked_network_stack(self):
        with tempfile.TemporaryDirectory() as directory:
            bundles = {}
            for name, required in [('display', ['graphics', 'pci', 'com.apple.kpi.iokit']),
                                   ('accelerator', ['graphics']), ('graphics', ['pci']),
                                   ('ethernet', ['network'])]:
                bundle = Path(directory)/(name+'.kext')
                contents = bundle/'Contents'
                contents.mkdir(parents=True)
                (contents/'Info.plist').write_bytes(plistlib.dumps(dict(
                    CFBundleIdentifier=name, OSBundleLibraries=dict.fromkeys(required, '1.0'))))
                bundles[name] = bundle
            self.assertEqual(module.dependencies(['display', 'accelerator', 'ethernet'],
                             {'pci', 'network', 'ethernet'}, bundles),
                             ['graphics', 'display', 'accelerator'])

    def test_missing_dependency_is_not_silently_omitted(self):
        with self.assertRaises(KeyError):
            module.dependencies(['missing'], set(), {})

    def test_container_without_plist_keeps_its_loadable_plugin(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plugin = root/'Container.kext/Contents/PlugIns/Driver.kext'
            (plugin/'Contents').mkdir(parents=True)
            (plugin/'Contents/Info.plist').write_bytes(plistlib.dumps(
                dict(CFBundleIdentifier='driver')))
            self.assertEqual(module.discover_bundles(root), {'driver': plugin})

    def test_esp_copy_preserves_bytes_without_filesystem_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root/'source'
            source.mkdir()
            (source/'binary').write_bytes(b'kernel executable')
            (source/'alias').symlink_to('binary')
            module.copy_to_esp(source, root/'esp')
            self.assertEqual((root/'esp/alias').read_bytes(), b'kernel executable')
            self.assertFalse((root/'esp/alias').is_symlink())


if __name__ == '__main__':
    unittest.main()
