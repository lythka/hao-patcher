"""Selector relocation must stay scoped to the identified native ES image."""
import importlib.util
from pathlib import Path
import struct
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1] / 'src'
for name in ('legacy_trackpad', 'endpoint_security'):
    spec = importlib.util.spec_from_file_location(name, ROOT / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
es = sys.modules['endpoint_security']
cache = sys.modules['legacy_trackpad']


class EndpointSecurityTest(unittest.TestCase):
    def family(self, root):
        main, sub = root/'dyld_shared_cache_x86_64', root/'dyld_shared_cache_x86_64.01'
        base, address, offset = 0x200000, 0x400000, 0x1000
        a, b = bytearray(0x1000), bytearray(0x8000)
        for data, vmaddr in ((a, base), (b, address-offset)):
            data[:16] = b'dyld_v1  x86_64\0'
            struct.pack_into('<II', data, 16, 512, 1)
            struct.pack_into('<QQQII', data, 512, vmaddr, len(data), 0, 5, 5)
        struct.pack_into('<II', a, 448, 600, 1)
        struct.pack_into('<QQQII', a, 600, address, 0, 0, 700, 0)
        a[700:700+len(es.IMAGE)+1] = es.IMAGE+b'\0'
        struct.pack_into('<8I', b, offset, 0xfeedfacf, 0x1000007, 3, 6, 2, 96, 0, 0)
        struct.pack_into('<II16s', b, offset+32, 0x1b, 24, es.UUID)
        struct.pack_into('<II16sQQQQIIII', b, offset+56, 0x19, 72,
                         b'__TEXT', address, 0x7000, offset, 0x7000, 5, 5, 0, 0)
        for relative, old, new in es.SELECTORS:
            b[offset+relative:offset+relative+5] = b'\xbe'+struct.pack('<I', old)
        main.write_bytes(a)
        sub.write_bytes(b)
        return main, sub, offset

    def test_relocates_only_native_library_operands_in_subcache(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            main, sub, offset = self.family(root)
            original_main, expected = main.read_bytes(), bytearray(sub.read_bytes())
            plan = es.plan_cache_family(root)
            self.assertEqual(set(plan), {sub})
            cache.apply_edits(sub, plan[sub])
            for relative, old, new in es.SELECTORS:
                expected[offset+relative+1:offset+relative+5] = struct.pack('<I', new)
            self.assertEqual(sub.read_bytes(), expected)
            self.assertEqual(main.read_bytes(), original_main)

    def test_refuses_identity_or_any_operand_mismatch_without_writing(self):
        for relative in [4, 40, 80] + [r for r, _, _ in es.SELECTORS]:
            with self.subTest(relative=relative), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                _, sub, offset = self.family(root)
                data = bytearray(sub.read_bytes())
                data[offset+relative] ^= 1
                sub.write_bytes(data)
                with self.assertRaises(ValueError):
                    es.plan_cache_family(root)
                self.assertEqual(sub.read_bytes(), data)

    def test_removed_slots_and_unsupported_new_methods(self):
        # Donor methods 5/6/8 were removed. Never accidentally dispatch there.
        destinations = {new for _, _, new in es.SELECTORS}
        self.assertFalse(destinations & {5, 6, 8})
        for _, old, new in es.SELECTORS:
            self.assertEqual(new, 7 if old == 5 else old+3)
            if old >= 25:
                self.assertGreaterEqual(new, 28)  # donor externalMethod rejects
