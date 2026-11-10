import importlib.util
from pathlib import Path
import struct
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))
import audio_no_console as audio


class Repair(unittest.TestCase):
    def setUp(self):
        self.base = 0x7ffb21908000
        self.data = bytearray(0x25000)
        struct.pack_into('<IIIIIIII', self.data, 0, 0xfeedfacf, 0x1000007,
                         3, 6, 2, 96, 0, 0)
        struct.pack_into('<II16sQQ', self.data, 32, 0x19, 72, b'__TEXT',
                         self.base, len(self.data))
        struct.pack_into('<II16s', self.data, 104, 0x1b, 24, audio.UUID)
        self.data[0x243ac:0x243b6] = bytes.fromhex('837dd4000f84ab010000')
        self.data[0x24561:0x2456b] = bytes.fromhex('488b35a8b3f21f4c89f7')
        self.data[audio.CALL_OFFSET:audio.CALL_OFFSET+6] = audio.BEFORE

    def read(self, address, size):
        off = address-self.base
        return bytes(self.data[off:off+size])

    def test_only_notification_call_changes(self):
        original = bytes(self.data)
        edits = audio.checked_edits(self.read, self.base)
        self.assertEqual(len(edits), 1)
        address, before, after, _ = edits[0]
        self.assertEqual(address, self.base + audio.CALL_OFFSET)
        self.assertEqual(before, audio.BEFORE)
        self.data[address-self.base:address-self.base+len(after)] = after
        self.assertEqual(self.data[:audio.CALL_OFFSET], original[:audio.CALL_OFFSET])
        self.assertEqual(self.data[audio.CALL_OFFSET+6:], original[audio.CALL_OFFSET+6:])
        self.assertEqual(audio.checked_edits(self.read, self.base), [])

    def test_unknown_uuid_refused(self):
        self.data[112] ^= 1
        with self.assertRaisesRegex(ValueError, 'UUID'):
            audio.checked_edits(self.read, self.base)

    def test_different_console_branch_refused(self):
        self.data[0x243b1] = 0x85
        with self.assertRaisesRegex(ValueError, 'conditional'):
            audio.checked_edits(self.read, self.base)

    def test_different_receiver_refused(self):
        self.data[0x2456a] = 0xf6
        with self.assertRaisesRegex(ValueError, 'preparation'):
            audio.checked_edits(self.read, self.base)

    def test_different_call_refused(self):
        self.data[audio.CALL_OFFSET] = 0xe8
        with self.assertRaisesRegex(ValueError, 'instruction'):
            audio.checked_edits(self.read, self.base)

    def test_arm64_refused(self):
        struct.pack_into('<I', self.data, 4, 0x100000c)
        with self.assertRaisesRegex(ValueError, 'x86_64'):
            audio.checked_edits(self.read, self.base)


if __name__ == '__main__':
    unittest.main()
