"""The MPSNDArrayMatrixMultiplication initializer repair in lib/mps_matrix_initializer.py: scalar checks of both
instruction predicates, exact matching, idempotence and refusal of other inputs."""
from pathlib import Path
import random
import struct
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import mps_matrix_initializer as patch


def skips_native_matrix(context, family):
    # Execute the two inspected x86 32-bit instructions and unsigned JA.
    add = int.from_bytes(context[12:13], "little", signed=True)
    compare = int.from_bytes(context[15:16], "little", signed=True)
    return ((family + add) & 0xffffffff) > (compare & 0xffffffff)


# Context RIP fields of the second inspected build (26B131), for the masked match.
B131 = bytes.fromhex(
    "4a8b043b8b80c005000083c0e683f8f77744488b3ddcd09837e8639c1f00488b35f0895738"
    "0f57c00f110424f20f1005e9131a004889c74c89f231c94531c04531c90f28c8ff152a56a537"
    "488b0d335ba93b4889040b")
CONTEXT, POINTERS, STRINGS = 0x2ee13, 0x30000, 0x30100


def place(data, address, context, at, selector=patch.SELECTOR, name=patch.CLASS):
    """Write context at text offset `at`, its selector/class refs resolving locally."""
    context = bytearray(context)
    refs = dict(selector_reference=POINTERS, class_reference=POINTERS + 8)
    for field, slot in refs.items():
        start = patch.FIELDS[field]
        struct.pack_into('<i', context, start, slot - (at + start + 4))
    data[at:at + len(context)] = context
    cls, ro = address + POINTERS + 16, address + POINTERS + 64
    struct.pack_into('<QQ', data, POINTERS, address + STRINGS, cls)
    struct.pack_into('<Q', data, POINTERS + 16 + 32, ro | 1)
    struct.pack_into('<Q', data, POINTERS + 64 + 24, address + STRINGS + 0x100)
    data[STRINGS:STRINGS + len(selector) + 1] = selector + b'\0'
    data[STRINGS + 0x100:STRINGS + 0x100 + len(name) + 1] = name + b'\0'
    return bytes(context)


class InitializerPatchTests(unittest.TestCase):
    def image(self, identity=bytes(range(16)), cpu=0x1000007, context=None, at=CONTEXT, **refs):
        address = 0x100000000
        data = bytearray(0x31000)
        struct.pack_into('<IIIIIIII', data, 0, 0xfeedfacf, cpu, 3, 6, 2, 176, 0, 0)
        struct.pack_into('<II16sQQQQIIII', data, 32, 0x19, 152, b'__TEXT',
                         address, len(data), 0, len(data), 5, 5, 1, 0)
        struct.pack_into('<16s16sQQ', data, 104, b'__text', b'__TEXT', address + 0x1000, 0x2f000)
        struct.pack_into('<II', data, 184, 0x1b, 24)
        data[192:208] = identity
        placed = place(data, address, patch.ORIGINAL if context is None else context, at, **refs)
        self.read = lambda location, size: bytes(data[location-address:location-address+size])
        self.pointer = lambda location: struct.unpack('<Q', self.read(location, 8))[0]
        return address, data, placed

    def edits(self, address):
        return patch.checked_edits(self.read, address, self.pointer)

    def test_checked_image_and_idempotence(self):
        for context, at in [(patch.ORIGINAL, 0x2ee43), (B131, 0x2ee13), (B131, 0x2d000)]:
            with self.subTest(at=hex(at)):
                address, data, placed = self.image(context=context, at=at)
                edits = self.edits(address)
                self.assertEqual(len(edits), 1)
                location, before, after, _ = edits[0]
                self.assertEqual(location, address + at)
                self.assertEqual(before, placed)
                self.assertEqual([i for i, (a, b) in enumerate(zip(before, after)) if a != b], [12, 15])
                data[location-address:location-address+len(after)] = after
                self.assertEqual(self.edits(address), [])

    def test_refuses_architecture(self):
        address, _, _ = self.image(cpu=0x100000c)
        with self.assertRaisesRegex(ValueError, 'x86_64'):
            self.edits(address)

    def test_missing_context_names_image(self):
        address, data, _ = self.image()
        data[CONTEXT + 4] ^= 1
        with self.assertRaisesRegex(ValueError, 'MISSING.*original=0.*000102030405'):
            self.edits(address)

    def test_refuses_other_selector_or_class(self):
        for refs in [dict(selector=patch.SELECTOR[:-1] + b'X'), dict(name=b'MPSMatrixSum')]:
            with self.subTest(**{k: v.decode() for k, v in refs.items()}):
                address, _, _ = self.image(**refs)
                with self.assertRaisesRegex(ValueError, 'MISSING.*differs'):
                    self.edits(address)

    def test_refuses_malformed_load_command(self):
        address, data, _ = self.image()
        struct.pack_into('<I', data, 36, 7)
        with self.assertRaises(ValueError):
            self.edits(address)

    def test_refuses_missing_text_section(self):
        address, data, _ = self.image()
        data[104:110] = b'__cstr'
        with self.assertRaisesRegex(ValueError, 'MISSING.*__text'):
            self.edits(address)

    def test_masked_fields_only(self):
        self.assertTrue(patch.patch_initializer(B131).changed)
        for start in patch.FIELDS.values():
            for at in range(start - 1, start + 5):
                altered = bytearray(patch.ORIGINAL)
                altered[at] ^= 0x40
                with self.subTest(at=at):
                    if start <= at < start + 4:
                        self.assertTrue(patch.patch_initializer(altered).changed)
                    elif any(s <= at < s + 4 for s in patch.FIELDS.values()):
                        continue
                    else:
                        with self.assertRaises(ValueError):
                            patch.patch_initializer(altered)

    def test_exact_two_byte_repair_and_idempotence(self):
        original = b"prefix" + patch.ORIGINAL + b"suffix"
        result = patch.patch_initializer(original)
        self.assertTrue(result.changed)
        self.assertEqual(result.offset, 6)
        self.assertEqual([i for i, (a, b) in enumerate(zip(original, result.data)) if a != b], [18, 21])
        self.assertEqual(result.data[:6], b"prefix")
        self.assertEqual(result.data[-6:], b"suffix")
        again = patch.patch_initializer(result.data)
        self.assertFalse(again.changed)
        self.assertEqual(again.data, result.data)

    def test_only_native_intel_family25_changes(self):
        rng = random.Random(26_428)
        # Explicit requested family range, all nearby unsigned wrap boundaries,
        # signed boundaries, and independently sampled full-width values.
        families = list(range(31)) + list(range(0xffffffe0, 0x100000000))
        families += [0x7fffffff, 0x80000000, 0x80000001]
        families += [rng.getrandbits(32) for _ in range(10000)]
        for family in families:
            with self.subTest(family=family):
                before = skips_native_matrix(patch.ORIGINAL, family)
                after = skips_native_matrix(patch.FIXED, family)
                self.assertEqual(before, 18 <= family <= 25)
                self.assertEqual(after, 18 <= family <= 24)
                self.assertEqual(before != after, family == 25)

    def test_refuses_missing_partial_and_altered_context(self):
        for data in [b"", patch.ORIGINAL[:-1], patch.ORIGINAL[1:],
                     patch.ORIGINAL[:30] + b"\x90" + patch.ORIGINAL[31:]]:
            with self.assertRaises(ValueError):
                patch.patch_initializer(data)

    def test_refuses_duplicate_and_mixed_states(self):
        mixed = bytearray(patch.ORIGINAL)
        mixed[patch.CMP_IMMEDIATE_OFFSET] = patch.FIXED[patch.CMP_IMMEDIATE_OFFSET]
        for data in [patch.ORIGINAL * 2, patch.FIXED * 2,
                     patch.ORIGINAL + patch.FIXED, B131 + patch.ORIGINAL, bytes(mixed)]:
            with self.assertRaises(ValueError):
                patch.patch_initializer(data)


if __name__ == "__main__":
    unittest.main()
