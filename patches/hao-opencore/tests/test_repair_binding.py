import hashlib
import importlib.util
import json
from pathlib import Path
import plistlib
import struct
import tempfile
import unittest

SRC = Path(__file__).resolve().parents[1]/'src'


def load(name):
    spec = importlib.util.spec_from_file_location(name, SRC/(name+'.py'))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


binding = load('repair_binding')
repairs = load('repairs')


def header(commands, filetype=2):
    body = b''.join(commands)
    return struct.pack('<8I', 0xfeedfacf, 0x1000007, 3, filetype,
                       len(commands), len(body), 0, 0)+body


def segment(name, address, offset, size, sections=b''):
    return struct.pack('<II16sQQQQiiII', 0x19, 72+len(sections), name.encode(), address,
                       size, offset, size, 7, 5, len(sections)//80, 0)+sections


def read_metadata(collection):
    return plistlib.loads(collection.read_bytes()[0x2000:0x3000].rstrip(b'\0'))


def write_metadata(collection, info):
    data = bytearray(collection.read_bytes())
    blob = plistlib.dumps(info)
    assert len(blob) < 0x1000
    data[0x2000:0x3000] = blob.ljust(0x1000, b'\0')
    collection.write_bytes(data)



def fixture(root):
    kernel_va, surface_va = 0xffffff8000200000, 0xffffff8000300000
    kernel_off, surface_off = 0x200, 0x600
    data = bytearray(0x3000)
    entries = []
    for name, address, offset in [('com.apple.kernel', kernel_va, kernel_off),
                                  ('com.apple.iokit.IOSurface', surface_va, surface_off)]:
        size = (32+len(name)+1+7)//8*8
        entries.append(struct.pack('<IIQQII', 0x80000035, size, address, offset, 32, 0)
                       + (name.encode()+b'\0').ljust(size-32, b'\0'))
        cmds = [struct.pack('<II16s', 0x1b, 24, bytes([offset//0x200])*16),
                segment('__TEXT', address, offset, 0x200)]
        if name == 'com.apple.kernel':
            cmds.append(segment('__DATA', address+0x1000, 0x1000, 0x100))
        blob = header(cmds)
        data[offset:offset+len(blob)] = blob
    blob = header(entries+[struct.pack('<II16s', 0x1b, 24, bytes(16)),
                           segment('__TEXT', 0xffffff8000100000, 0, 0x200),
                           segment('__PRELINK_INFO', 0xffffff8000400000, 0x2000, 0x1000,
                                   struct.pack('<16s16sQQIIIIIIII', b'__info', b'__PRELINK_INFO',
                                               0xffffff8000400000, 0x1000, 0x2000, 0, 0, 0, 0, 0, 0, 0))], 0xc)
    data[:len(blob)] = blob
    capture = dict(kernel_uuid='01'*16,
                   collection_sha256='', site_address=hex(kernel_va+0x100),
                   site_offset=hex(kernel_off+0x100), original=b'initialize!'.hex(),
                   addresses=dict(global_pointer=hex(kernel_va+0x1000)))
    contexts = [('initializer', kernel_off+0x100, b'initialize!'),
                ('range', kernel_off+0x120, b'range-call!'),
                ('cave', kernel_off+0x140, b'zero-cave!'),
                ('hook', kernel_off+0x160, b'hook-code!')]
    patches = []
    for name, offset, original in contexts:
        data[offset:offset+len(original)] = original
        if name != 'initializer':
            value = dict(address=hex(kernel_va+offset-kernel_off), offset=hex(offset), original=original.hex())
            if name == 'range':
                capture['range_call'] = value
            else:
                capture.setdefault('pv_aliases', {})[name] = value
        patches.append(dict(Comment=name, Identifier='kernel', Find=original,
                            Replace=bytes(v ^ 255 for v in original)))
    collection = root/'BootKC'
    collection.write_bytes(data)
    path = '/private/tmp/reference/System/Library/Extensions/Demo.kext'
    write_metadata(collection, dict(_PrelinkKCID=bytes(16), _PrelinkInfoDictionary=[dict(
        CFBundleIdentifier='test.codeless', CFBundleVersion='1', OSBundleLibraries={'test.library': '1'},
        _CodelessKextBundlePath=path, _PrelinkExecutableLoadAddr=0x7fffffffffffffff,
        kOSBundleDextUniqueIdentifier=hashlib.sha256(path.encode()).digest())]))
    capture['collection_sha256'] = binding.sha(collection.read_bytes())
    artifact = root/'artifact'
    (artifact/'EFI/OC/Kexts/IntelCommpage.kext/Contents/MacOS').mkdir(parents=True)
    (artifact/'EFI/OC/Kexts/IntelCommpage.kext/Contents/MacOS/IntelCommpage').write_bytes(b'authored-kext')
    (artifact/'EFI/OC/config.plist').write_bytes(plistlib.dumps(dict(Kernel=dict(
        Patch=patches, Add=[dict(BundlePath='IntelCommpage.kext')]))))
    repair = dict(capture=capture, capture_sha256='reference-capture-sha',
                  replacements=[dict(comment=p['Comment'], find=p['Find'].hex(), replace=p['Replace'].hex()) for p in patches])
    (artifact/'build.json').write_text(json.dumps(dict(inputs=dict(early_vm_repair=repair))))
    abi = root/'commpage_abi.hpp'
    abi.write_text(f'addr_global={kernel_va+0x1000:#x}; addr_iosurface={surface_va+0x100:#x};')
    profile_path = root/'profile.json'
    profile_path.write_text(json.dumps(binding.generate_profile(artifact, collection, abi)))
    return artifact, collection, profile_path


class RepairBinding(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.artifact, self.kc, self.profile = fixture(self.root)

    def bind(self):
        return binding.bind(self.profile, self.artifact, self.kc, self.root/'bound')

    def mutate(self, offset):
        data = bytearray(self.kc.read_bytes())
        data[offset] ^= 1
        self.kc.write_bytes(data)
        if offset == 0xa0:
            info = read_metadata(self.kc)
            info['_PrelinkKCID'] = binding.outer_uuid(data)
            write_metadata(self.kc, info)


    def test_outer_metadata_change_creates_full_hash_bound_artifact_and_installs(self):
        self.mutate(0xa0)  # Enclosing KC metadata outside fileset members.
        record = self.bind()
        bound = self.root/'bound'
        digest = hashlib.sha256(self.kc.read_bytes()).hexdigest()
        self.assertEqual(record['inputs']['early_vm_repair']['capture']['collection_sha256'], digest)
        capture_bytes = (bound/'capture.json').read_bytes()
        self.assertEqual(record['inputs']['early_vm_repair']['capture_sha256'], binding.sha(capture_bytes))
        self.assertEqual(json.loads(capture_bytes), record['inputs']['early_vm_repair']['capture'])
        self.assertEqual((bound/'reference-build.json').read_bytes(), (self.artifact/'build.json').read_bytes())
        self.assertNotIn('build.json', record['files'])
        self.assertEqual(set(record['files']), {str(p.relative_to(bound)) for p in bound.rglob('*') if p.is_file()} - {'build.json'})
        for path, digest_ in record['files'].items():
            self.assertEqual(binding.sha((bound/path).read_bytes()), digest_)
        proof = record['inputs']['repair_binding']
        self.assertEqual(proof['fileset_members'], 2)
        self.assertNotEqual(proof['reference_collection_sha256'], digest)
        self.assertEqual(proof['target_collection_sha256'], digest)
        for path, expected in proof['unchanged_payload_files'].items():
            if path != 'build.json':
                self.assertEqual(hashlib.sha256((bound/path).read_bytes()).hexdigest(), expected)
        target = self.root/'target.plist'
        target.write_bytes(plistlib.dumps(dict(Kernel=dict(Patch=[dict(Comment='keep')], Add=[]))))
        repairs.apply(target, bound, self.kc)
        config = plistlib.loads(target.read_bytes())
        self.assertEqual(len(config['Kernel']['Patch']), 5)
        self.assertEqual(config['Kernel']['Patch'][0]['Comment'], 'keep')
        self.mutate(0xa0)
        with self.assertRaisesRegex(ValueError, 'Target BootKC differs'):
            repairs.apply(target, bound, self.kc)

    def test_changed_unbound_outer_code_refused(self):
        self.mutate(0x180)
        with self.assertRaisesRegex(ValueError, 'outside UUID/prelink metadata'):
            self.bind()

    def test_changed_prelink_paths_allowed(self):
        info = read_metadata(self.kc)
        entry = info['_PrelinkInfoDictionary'][0]
        entry['_CodelessKextBundlePath'] = '/Volumes/new/job/mnt/system/System/Library/Extensions/Demo.kext'
        entry['kOSBundleDextUniqueIdentifier'] = hashlib.sha256(entry['_CodelessKextBundlePath'].encode()).digest()
        write_metadata(self.kc, info)
        self.bind()

    def test_nonpath_prelink_dependency_change_refused(self):
        info = read_metadata(self.kc)
        info['_PrelinkInfoDictionary'][0]['OSBundleLibraries']['test.library'] = '2'
        write_metadata(self.kc, info)
        with self.assertRaisesRegex(ValueError, 'Non-path prelink metadata differs'):
            self.bind()

    def test_nonpath_prelink_load_address_change_refused(self):
        info = read_metadata(self.kc)
        info['_PrelinkInfoDictionary'][0]['_PrelinkExecutableLoadAddr'] = 123
        write_metadata(self.kc, info)
        with self.assertRaisesRegex(ValueError, 'Non-path prelink metadata differs'):
            self.bind()

    def test_path_paired_id_must_be_exact_sha256(self):
        info = read_metadata(self.kc)
        info['_PrelinkInfoDictionary'][0]['kOSBundleDextUniqueIdentifier'] = bytes(32)
        write_metadata(self.kc, info)
        with self.assertRaisesRegex(ValueError, 'ID is not the hash'):
            self.bind()

    def test_path_suffix_change_refused(self):
        info = read_metadata(self.kc)
        entry = info['_PrelinkInfoDictionary'][0]
        entry['_CodelessKextBundlePath'] += '/Changed.kext'
        entry['kOSBundleDextUniqueIdentifier'] = hashlib.sha256(entry['_CodelessKextBundlePath'].encode()).digest()
        write_metadata(self.kc, info)
        with self.assertRaisesRegex(ValueError, 'path set differs'):
            self.bind()


    def golden_metadata_profile(self):
        metadata = self.root/'GoldenKC'
        metadata.write_bytes(self.kc.read_bytes())
        info = read_metadata(metadata)
        info['_PrelinkInfoDictionary'][0]['CFBundleVersion'] = '2'
        write_metadata(metadata, info)
        profile = binding.generate_profile(self.artifact, self.kc, self.root/'commpage_abi.hpp', metadata)
        self.profile.write_text(json.dumps(profile))
        return metadata

    def test_metadata_baseline_cannot_change_compiled_code(self):
        metadata = self.root/'GoldenKC'
        data = bytearray(self.kc.read_bytes())
        data[0x700] ^= 1
        metadata.write_bytes(data)
        with self.assertRaisesRegex(ValueError, 'changes compiled collection bindings'):
            binding.generate_profile(self.artifact, self.kc, self.root/'commpage_abi.hpp', metadata)

    def test_exact_legacy_hash_accepted_with_new_golden_metadata_profile(self):
        self.golden_metadata_profile()
        record = self.bind()
        self.assertEqual(record['inputs']['repair_binding']['binding_mode'], 'exact-reference')

    def test_new_kc_requires_and_accepts_exact_reviewed_golden_metadata(self):
        metadata = self.golden_metadata_profile()
        self.kc.write_bytes(metadata.read_bytes())
        self.mutate(0xa0)
        record = self.bind()
        self.assertEqual(record['inputs']['repair_binding']['binding_mode'], 'verified-metadata-baseline')

    def test_new_kc_with_unreviewed_legacy_metadata_rejected(self):
        self.golden_metadata_profile()
        self.mutate(0xa0)
        with self.assertRaisesRegex(ValueError, 'Non-path prelink metadata differs'):
            self.bind()

    def test_changed_kernel_code_refused(self):
        self.mutate(0x300)
        with self.assertRaisesRegex(ValueError, 'fileset binding differs.*com.apple.kernel'):
            self.bind()
        self.assertFalse((self.root/'bound').exists())

    def test_changed_kernel_global_refused(self):
        self.mutate(0x1000)
        with self.assertRaisesRegex(ValueError, 'fileset binding differs'):
            self.bind()

    def test_changed_segment_map_refused(self):
        # __DATA vmaddr in its load command, with every code/global byte unchanged.
        self.mutate(0x200+32+24+72+24)
        with self.assertRaisesRegex(ValueError, 'fileset binding differs'):
            self.bind()

    def test_changed_iosurface_refused(self):
        self.mutate(0x700)
        with self.assertRaisesRegex(ValueError, 'fileset binding differs.*IOSurface'):
            self.bind()

    def test_changed_artifact_refused(self):
        executable = self.artifact/'EFI/OC/Kexts/IntelCommpage.kext/Contents/MacOS/IntelCommpage'
        executable.write_bytes(b'changed-kext')
        with self.assertRaisesRegex(ValueError, 'payload differs'):
            self.bind()

    def test_find_must_remain_unique_even_in_outer_metadata(self):
        data = bytearray(self.kc.read_bytes())
        data[0x1800:0x180b] = b'initialize!'
        self.kc.write_bytes(data)
        with self.assertRaisesRegex(ValueError, 'Find is not unique'):
            self.bind()

    def test_profile_rejects_wrong_reference_capture(self):
        self.mutate(0xa0)
        with self.assertRaisesRegex(ValueError, 'differs from the recorded capture'):
            binding.generate_profile(self.artifact, self.kc, self.root/'commpage_abi.hpp')

    def test_profile_rejects_unbound_compiled_address(self):
        abi = self.root/'commpage_abi.hpp'
        abi.write_text('addr_missing=0xffffff8000999999;')
        with self.assertRaisesRegex(ValueError, 'outside all fileset segments'):
            binding.generate_profile(self.artifact, self.kc, abi)

    def test_existing_output_is_not_overwritten(self):
        self.bind()
        with self.assertRaises(FileExistsError):
            self.bind()


if __name__ == '__main__':
    unittest.main()
