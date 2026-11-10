import importlib.util
from pathlib import Path
import struct
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]/'src'
spec = importlib.util.spec_from_file_location('legacy_trackpad', ROOT/'legacy_trackpad.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
spec = importlib.util.spec_from_file_location('cache_pages', ROOT/'cache_pages.py')
pages = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pages)


class TrackpadTest(unittest.TestCase):
    def family(self, root):
        # Put the image table in main and the Mach-O/code in a subcache: virtual
        # addresses must not be mistaken for file offsets or extracted offsets.
        main, sub = root/'dyld_shared_cache_x86_64', root/'dyld_shared_cache_x86_64.01'
        base, image, image_file = 0x200000, 0x400000, 0x1000
        a, b = bytearray(0x1000), bytearray(0x12000)
        for data, address in [(a, base), (b, image-image_file)]:
            data[:16] = b'dyld_v1  x86_64\0'
            struct.pack_into('<II', data, 16, 512, 1)
            struct.pack_into('<QQQII', data, 512, address, len(data), 0, 5, 5)
        struct.pack_into('<II', a, 448, 600, 1)
        struct.pack_into('<QQQII', a, 600, image, 0, 0, 700, 0)
        a[700:700+len(module.IMAGE)+1] = module.IMAGE+b'\0'
        struct.pack_into('<8I', b, image_file, 0xfeedfacf, 0x1000007, 3, 6, 2, 96, 0, 0)
        struct.pack_into('<II16s', b, image_file+32, 0x1b, 24, module.UUID)
        struct.pack_into('<II16sQQQQIIII', b, image_file+56, 0x19, 72,
                         b'__TEXT', image, 0x11000, image_file, 0x11000, 5, 5, 0, 0)
        for offset, old, _, _ in module.edits():
            b[image_file+offset:image_file+offset+len(old)] = old
        main.write_bytes(a); sub.write_bytes(b)
        return main, sub, image_file

    def test_resolves_subcache_and_changes_only_planned_instruction_ranges(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            main, sub, offset = self.family(root)
            before, main_before = sub.read_bytes(), main.read_bytes()
            plan = module.plan_cache_family(root)
            self.assertEqual(set(plan), {sub})
            module.apply_edits(sub, plan[sub])
            expected = bytearray(before)
            for relative, old, new, _ in module.edits():
                expected[offset+relative:offset+relative+len(old)] = new
            self.assertEqual(sub.read_bytes(), expected)
            self.assertEqual(main.read_bytes(), main_before)

    def test_refuses_wrong_uuid_and_each_changed_instruction_before_writing(self):
        for changed in [40] + [relative for relative, _, _, _ in module.edits()]:
            with self.subTest(changed=changed), tempfile.TemporaryDirectory() as d:
                root = Path(d)
                _, sub, offset = self.family(root)
                data = bytearray(sub.read_bytes()); data[offset+changed] ^= 1
                sub.write_bytes(data)
                with self.assertRaises(ValueError): module.plan_cache_family(root)
                self.assertEqual(sub.read_bytes(), data)

    def test_apply_does_not_write_first_edit_when_later_edit_changed(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _, sub, _ = self.family(root)
            plan = module.plan_cache_family(root)
            data = bytearray(sub.read_bytes()); data[plan[sub][-1][0]] ^= 1
            sub.write_bytes(data)
            with self.assertRaises(ValueError): module.apply_edits(sub, plan[sub])
            self.assertEqual(sub.read_bytes(), data)

    def test_forced_signing_updates_an_existing_4k_signature(self):
        # Construct the original 16 KiB CodeDirectory shape used by the signer.
        import hashlib
        code = bytearray(0x4000)
        sig = bytearray(0x4000)
        struct.pack_into('<QQ', code, 0x28, len(code), len(sig))
        struct.pack_into('>III', sig, 0, 0xfade0cc0, len(sig), 1)
        struct.pack_into('>II', sig, 12, 0, 20)
        struct.pack_into('>9I4B', sig, 20, 0xfade0c02, 160, 0x20400, 2,
                         128, 88, 0, 1, len(code), 32, 2, 0, 14)
        struct.pack_into('>Q', sig, 20+72, len(code))
        sig[20+88:20+93] = b'test\0'
        signed = bytearray(pages.resign_4k(code+sig))
        signed[0x1001] ^= 1
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)/'cache'; path.write_bytes(signed)
            self.assertFalse(pages.resign_file(path))
            self.assertTrue(pages.resign_file(path, force=True))
            data = path.read_bytes()
            signature = struct.unpack_from('<Q', data, 0x28)[0]
            cd = signature+36
            hashes = struct.unpack_from('>I', data, cd+16)[0]
            for page in range(signature//4096):
                self.assertEqual(data[cd+hashes+page*32:cd+hashes+(page+1)*32],
                                 hashlib.sha256(data[page*4096:(page+1)*4096]).digest())
