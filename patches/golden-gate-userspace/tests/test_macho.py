import importlib.util
from pathlib import Path
import struct
import unittest

spec = importlib.util.spec_from_file_location('macho_merge', Path(__file__).resolve().parents[1]/'src/macho.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def thin(cpu, subtype, body):
    return struct.pack('<IIIIIIII', 0xfeedfacf, cpu, subtype, 6, 0, 0, 0, 0)+body


class MergeTest(unittest.TestCase):
    def test_preserves_each_signed_slice_byte_for_byte(self):
        arm = thin(0x100000c, 0x80000002, b'ARM image and embedded signature')
        intel = thin(0x1000007, 3, b'Intel image and embedded signature')
        result = module.slices(module.combine(arm, intel))
        self.assertEqual(result, {(0x100000c, 0x80000002): arm, (0x1000007, 3): intel})

    def test_keeps_existing_architecture_and_adds_only_missing_slices(self):
        base = thin(0x1000007, 3, b'original')
        self.assertEqual(module.combine(base, thin(0x1000007, 3, b'alternate')), base)

    def test_does_not_treat_cache_or_resource_files_as_macho(self):
        self.assertIsNone(module.combine(b'dyld_v1 x86_64', b'plain resources'))
