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


class InitializerPatchTests(unittest.TestCase):
    def image(self, identity=None, cpu=0x1000007, context=None):
        address = 0x100000000
        data = bytearray(patch.CONTEXT_OFFSET + len(patch.ORIGINAL))
        struct.pack_into('<IIIIIIII', data, 0, 0xfeedfacf, cpu, 3, 6, 2, 96, 0, 0)
        struct.pack_into('<II', data, 32, 0x1b, 24)
        data[40:56] = patch.UUID if identity is None else identity
        struct.pack_into('<II16sQQ', data, 56, 0x19, 72, b'__TEXT', address, len(data))
        data[patch.CONTEXT_OFFSET:] = patch.ORIGINAL if context is None else context
        return address, data

    def test_checked_image_and_idempotence(self):
        address, data = self.image()
        read = lambda location, size: bytes(data[location-address:location-address+size])
        edits = patch.checked_edits(read, address)
        self.assertEqual(len(edits), 1)
        location, before, after, _ = edits[0]
        self.assertEqual(location, address + patch.CONTEXT_OFFSET)
        self.assertEqual(before, patch.ORIGINAL)
        data[location-address:location-address+len(after)] = after
        self.assertEqual(patch.checked_edits(read, address), [])

    def test_refuses_other_image_identity_and_architecture(self):
        for kwargs in [dict(identity=bytes(16)), dict(cpu=0x100000c)]:
            address, data = self.image(**kwargs)
            with self.assertRaises(ValueError):
                patch.checked_edits(lambda a, n: bytes(data[a-address:a-address+n]), address)

    def test_refuses_malformed_load_command(self):
        address, data = self.image()
        struct.pack_into('<I', data, 36, 7)
        with self.assertRaises(ValueError):
            patch.checked_edits(lambda a, n: bytes(data[a-address:a-address+n]), address)

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
        for data in [patch.ORIGINAL * 2, patch.FIXED * 2,
                     patch.ORIGINAL + patch.FIXED]:
            with self.assertRaises(ValueError):
                patch.patch_initializer(data)


if __name__ == "__main__":
    unittest.main()
