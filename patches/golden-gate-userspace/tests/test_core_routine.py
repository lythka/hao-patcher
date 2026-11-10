import importlib.util
from pathlib import Path
import struct
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1] / 'src'
for name in ('legacy_trackpad', 'core_routine'):
    spec = importlib.util.spec_from_file_location(name, ROOT / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
routine = sys.modules['core_routine']


class CoreRoutineTest(unittest.TestCase):
    base = 0x400000

    def fixture(self):
        data = bytearray(0x92000)
        struct.pack_into('<8I', data, 0, 0xfeedfacf, 0x1000007, 3, 6, 2, 176, 0, 0)
        struct.pack_into('<II16s', data, 32, 0x1b, 24, routine.UUID)
        struct.pack_into('<II16sQQQQIIII', data, 56, 0x19, 152,
                         b'__TEXT', self.base, len(data), 0, len(data), 5, 5, 1, 0)
        struct.pack_into('<16s16sQQ', data, 128, b'__text', b'__TEXT',
                         self.base+0x11c8, 0x7b56c)
        data[routine.CALL-8:routine.CALL+5] = (
            bytes.fromhex('488d75c431ff31d2')+routine.branch(0xe8, routine.CALL, routine.STUB))
        data[0xeed7:0xeedd] = bytes.fromhex('837dc400742d')
        return data

    def plan(self, data):
        return routine.checked_edits(lambda addr, size: data[addr-self.base:addr-self.base+size], self.base)

    def test_call_preserves_return_address_and_tail_jumps_to_original_api(self):
        edits = self.plan(self.fixture())
        cave, call = edits
        self.assertEqual(call[2][0], 0xe8)
        self.assertEqual(call[0]+5+struct.unpack('<i', call[2][1:])[0], cave[0])
        self.assertEqual(cave[2][:6], bytes.fromhex('c70600000000'))
        self.assertEqual(cave[2][6], 0xe9)
        self.assertEqual(cave[0]+11+struct.unpack('<i', cave[2][7:])[0], self.base+routine.STUB)

    def test_rejects_identity_code_guard_and_occupied_padding(self):
        for offset in [4, 40, routine.CAVE, routine.CALL-8, routine.CALL, 0xeed7]:
            with self.subTest(offset=offset):
                data = self.fixture()
                data[offset] ^= 1
                with self.assertRaises(ValueError):
                    self.plan(data)

    def test_rejects_padding_overlapping_load_commands_or_section(self):
        data = self.fixture()
        struct.pack_into('<I', data, 20, routine.CAVE)
        with self.assertRaises(ValueError):
            self.plan(data)
        data = self.fixture()
        struct.pack_into('<Q', data, 160, self.base+routine.CAVE)
        with self.assertRaises(ValueError):
            self.plan(data)
