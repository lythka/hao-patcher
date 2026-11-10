import struct
import unittest

import compiler_mask


def fixture():
    # A relocatable file layout ensures the repair follows the symbol rather
    # than relying on the address in the investigated Apple build.
    data = bytearray(1024)
    struct.pack_into('<IIIIIIII', data, 0, 0xfeedfacf, 0x1000007, 3, 6, 2, 96, 0, 0)
    struct.pack_into('<II16sQQQQIIII', data, 32, 0x19, 72, b'__TEXT', 4096, 512, 128, 512, 7, 5, 0, 0)
    struct.pack_into('<IIIIII', data, 104, 2, 24, 768, 2, 800, 150)
    names = b'\0_' + compiler_mask.SYMBOL.encode() + b'\0_next\0'
    data[800:800+len(names)] = names
    struct.pack_into('<IBBHQ', data, 768, 1, 0xf, 1, 0, 4096 + 128)
    struct.pack_into('<IBBHQ', data, 784, len(names)-6, 0xf, 1, 0, 4096 + 256)
    condition = bytes.fromhex('0d0000000241894630')
    immediate = bytes.fromhex('be01000000ba01000000b9020000004531c0')
    data[260:260+len(condition)] = condition
    data[300:300+len(immediate)] = immediate
    # The same byte sequence outside the target function must stay untouched.
    data[400:400+len(condition)] = condition
    return bytes(data)


class CompilerMaskTests(unittest.TestCase):
    def test_patch_follows_function_and_changes_only_condition_and_immediate(self):
        original = fixture()
        fixed, report = compiler_mask.repair(original)
        self.assertEqual([i for i, (a, b) in enumerate(zip(original, fixed)) if a != b], [264, 306])
        self.assertEqual(fixed[264], 4)
        self.assertEqual(fixed[306], 0)
        self.assertEqual(report['function'], compiler_mask.SYMBOL)

    def test_does_not_patch_an_unrecognized_emitter_or_patch_twice(self):
        original = fixture()
        fixed, _ = compiler_mask.repair(original)
        for data in (fixed, original[:264]+b'\x01'+original[265:], b'not a compiler'):
            with self.subTest(), self.assertRaises(ValueError):
                compiler_mask.repair(data)
