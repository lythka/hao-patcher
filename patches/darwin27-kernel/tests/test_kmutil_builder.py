"""src/kmutil_builder.py: the thin x86_64 kmutil that loads Tahoe's kernel-collection builder on an Intel host.
python3.14 -I -B -m unittest discover -s patches/darwin27-kernel/tests

Synthetic universal and thin Mach-Os always; with DARWIN27_KERNEL_KMUTIL=PATH (the tahoe_kmutil row's file) also
Tahoe's own kmutil, whose x86_64 slice then names @executable_path/kcbuilder.dylib and no longer the system builder."""
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest

PATCH = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PATCH / 'src'))
import kmutil_builder as KB  # noqa: E402

NEW = b'@executable_path/kcbuilder.dylib'


def thin(cpu, names):
    """A minimal 64-bit Mach-O for CPU whose load commands are one LC_LOAD_DYLIB per NAMES entry."""
    cmds = b''
    for name in names:
        body = name + b'\0' * (-(24 + len(name) + 1) % 8 + 1)
        cmds += struct.pack('<IIIIII', 0xC, 24 + len(body), 24, 2, 0x10000, 0x10000) + body
    return struct.pack('<IiiIIIII', KB.MH_MAGIC_64, cpu, 3, 2, len(names), len(cmds), 0, 0) + cmds


def fat(*slices):
    head = struct.pack('>II', KB.FAT_MAGIC, len(slices))
    at, body, table = 0x4000, b'', b''
    for cpu, data in slices:
        table += struct.pack('>iiIII', cpu, 3, at + len(body), len(data), 14)
        body += data + b'\0' * (-len(data) % 0x4000)
    return (head + table).ljust(0x4000, b'\0') + body


class Repoint(unittest.TestCase):
    def run_tool(self, data):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'kmutil'
            p.write_bytes(data)
            KB.main(str(p), NEW.decode())
            return p.read_bytes()

    def names(self, macho):
        out, at = [], 32
        for _ in range(struct.unpack_from('<I', macho, 16)[0]):
            cmd, size = struct.unpack_from('<II', macho, at)
            if cmd in KB.LOAD_COMMANDS:
                out.append(bytes(macho[at + 24:at + size]).split(b'\0', 1)[0])
            at += size
        return out

    def test_universal_becomes_thin_x86_64(self):
        x86 = thin(KB.CPU_TYPE_X86_64, [b'/usr/lib/libSystem.B.dylib', KB.BUILDER])
        arm = thin(0x0100000C, [KB.BUILDER])
        out = self.run_tool(fat((KB.CPU_TYPE_X86_64, x86), (0x0100000C, arm)))
        self.assertEqual(struct.unpack_from('<Ii', out), (KB.MH_MAGIC_64, KB.CPU_TYPE_X86_64))
        self.assertEqual(self.names(out), [b'/usr/lib/libSystem.B.dylib', NEW])
        self.assertEqual(len(out), len(x86))

    def test_thin_input(self):
        out = self.run_tool(thin(KB.CPU_TYPE_X86_64, [KB.BUILDER]))
        self.assertEqual(self.names(out), [NEW])

    def test_refusals(self):
        with self.assertRaises(SystemExit):     # no builder to repoint (or already repointed)
            self.run_tool(thin(KB.CPU_TYPE_X86_64, [b'/usr/lib/libSystem.B.dylib']))
        with self.assertRaises(SystemExit):     # no x86_64 slice
            self.run_tool(fat((0x0100000C, thin(0x0100000C, [KB.BUILDER]))))
        with self.assertRaises(SystemExit):     # the new path must fit the command
            macho = bytearray(thin(KB.CPU_TYPE_X86_64, [KB.BUILDER]))
            KB.repoint(macho, b'@executable_path/' + b'x' * 64)

    @unittest.skipUnless(os.environ.get('DARWIN27_KERNEL_KMUTIL'), 'DARWIN27_KERNEL_KMUTIL not set')
    def test_tahoe_kmutil(self):
        out = self.run_tool(Path(os.environ['DARWIN27_KERNEL_KMUTIL']).read_bytes())
        names = self.names(out)
        self.assertIn(NEW, names)
        self.assertNotIn(KB.BUILDER, names)
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'kmutil'
            p.write_bytes(out)
            subprocess.run(['codesign', '-s', '-', '-f', str(p)], check=True, capture_output=True)


if __name__ == '__main__':
    unittest.main()
