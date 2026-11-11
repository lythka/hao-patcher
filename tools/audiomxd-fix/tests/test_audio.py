from pathlib import Path
import struct
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))
import audio_no_console as audio

# Code shapes of the two inspected builds, relative to __TEXT. Both put the
# compare at 0x243ac and the send at 0x24561; only RIP displacements differ.
BUILDS = {
    '26A428': ('34D52126665133DEBB426B17B8C547E1', 'a8b3f21f', '8738771e'),
    '26B131': ('F30AFE52C5E1356A87671F8E2FD2485E', '58111c21', 'cf6ff11d'),
}
BRANCH, SEND = 0x243ac, 0x24561
STRINGS = 0x26000


class Repair(unittest.TestCase):
    def image(self, build='26A428', send=SEND, selector=audio.SELECTOR):
        identity, selref, got = BUILDS[build]
        self.base = 0x7ffb21908000
        self.data = bytearray(0x27000)
        struct.pack_into('<IIIIIIII', self.data, 0, 0xfeedfacf, 0x1000007,
                         3, 6, 2, 176, 0, 0)
        struct.pack_into('<II16sQQQQIIII', self.data, 32, 0x19, 152, b'__TEXT',
                         self.base, len(self.data), 0, len(self.data), 5, 5, 1, 0)
        struct.pack_into('<16s16sQQ', self.data, 104, b'__text', b'__TEXT',
                         self.base + 0x1000, 0x24000)
        struct.pack_into('<II16s', self.data, 184, 0x1b, 24, bytes.fromhex(identity))
        self.data[BRANCH:BRANCH+10] = b'\x83\x7d\xd4\x00\x0f\x84' + struct.pack(
            '<i', send - (BRANCH + 10))
        # The selector reference sits at the RIP target of the movq; it holds
        # an encoded pointer to the selector string.
        disp = struct.unpack('<i', bytes.fromhex(selref))[0] & 0xfff
        self.reference = send + 7 + disp
        self.data[send:send+16] = (b'\x48\x8b\x35' + struct.pack('<i', disp) +
                                   b'\x4c\x89\xf7\xff\x15' + bytes.fromhex(got))
        struct.pack_into('<Q', self.data, self.reference, 0x5100000000 | STRINGS)
        self.data[STRINGS:STRINGS+len(selector)+1] = selector + b'\0'
        return send + 10

    def read(self, address, size):
        off = address-self.base
        if off < 0 or off + size > len(self.data):
            raise ValueError('unmapped')
        return bytes(self.data[off:off+size])

    def pointer(self, address):
        return self.base + (struct.unpack('<Q', self.read(address, 8))[0] & 0xffffffff)

    def edits(self):
        return audio.checked_edits(self.read, self.base, self.pointer)

    def test_both_builds_change_only_notification_call(self):
        for build in BUILDS:
            with self.subTest(build=build):
                call = self.image(build)
                original = bytes(self.data)
                edits = self.edits()
                self.assertEqual(len(edits), 1)
                address, before, after, _ = edits[0]
                self.assertEqual(address, self.base + call)
                self.assertEqual(before, b'\xff\x15' + bytes.fromhex(BUILDS[build][2]))
                self.assertEqual(after, audio.AFTER)
                self.data[call:call+6] = after
                self.assertEqual(self.data[:call], original[:call])
                self.assertEqual(self.data[call+6:], original[call+6:])
                self.assertEqual(self.edits(), [])

    def test_relocated_code_is_found(self):
        call = self.image('26B131', send=SEND + 0x40)
        self.assertEqual(self.edits()[0][0], self.base + call)

    def test_other_selector_missing(self):
        self.image(selector=b'_handleServerDeat')
        with self.assertRaisesRegex(ValueError, 'MISSING.*34D52126.*found 0'):
            self.edits()

    def test_different_console_branch_missing(self):
        self.image()
        self.data[BRANCH+5] = 0x85
        with self.assertRaisesRegex(ValueError, 'MISSING'):
            self.edits()

    def test_different_receiver_missing(self):
        self.image()
        self.data[SEND+8] = 0x88
        with self.assertRaisesRegex(ValueError, 'MISSING'):
            self.edits()

    def test_different_call_missing(self):
        self.image()
        self.data[SEND+10] = 0xe8
        with self.assertRaisesRegex(ValueError, 'MISSING'):
            self.edits()

    def test_two_candidates_refused(self):
        self.image()
        second = 0x20000
        self.data[second:second+10] = b'\x83\x7d\xd0\x00\x0f\x84' + struct.pack(
            '<i', second + 0x100 - (second + 10))
        disp = self.reference - (second + 0x100 + 7)
        self.data[second+0x100:second+0x110] = (b'\x48\x8b\x35' + struct.pack('<i', disp) +
                                                self.data[SEND+7:SEND+16])
        with self.assertRaisesRegex(ValueError, 'found 2'):
            self.edits()

    def test_missing_text_section(self):
        self.image()
        self.data[104:110] = b'__cstr'
        with self.assertRaisesRegex(ValueError, 'MISSING.*__text'):
            self.edits()

    def test_arm64_refused(self):
        self.image()
        struct.pack_into('<I', self.data, 4, 0x100000c)
        with self.assertRaisesRegex(ValueError, 'x86_64'):
            self.edits()


if __name__ == '__main__':
    unittest.main()
