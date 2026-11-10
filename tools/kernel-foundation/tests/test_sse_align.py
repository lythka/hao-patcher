"""Host tests for src/sse_align.h (IntelCommpage's misaligned-SSE #GP fix-up): encodings come from the assembler."""
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
ACCEPT = {   # instruction -> (base, index, scale, disp, store)
    'movapd 0x328(%r14), %xmm2': (14, -1, 1, 0x328, 0),          # VectorKit MercatorCameraContext ctor
    'mulpd 0x10(%rax,%rbx,8), %xmm1': (0, 3, 8, 0x10, 0),
    'addpd -0x8(%r9), %xmm15': (9, -1, 1, -8, 0),
    'movaps %xmm0, 0x8(%rdi)': (7, -1, 1, 8, 1),
    'movdqa %xmm3, (%rsi)': (6, -1, 1, 0, 1),
    'movdqa 0x18(%rdx), %xmm4': (2, -1, 1, 0x18, 0),
    'pshufd $0x1b, 0x8(%rcx), %xmm2': (1, -1, 1, 8, 0),
    'paddd 0x1000(%r12,%r13,4), %xmm9': (12, 13, 4, 0x1000, 0),
    'pshufb 0x8(%rbx), %xmm0': (3, -1, 1, 8, 0),
    'blendps $0x3, 0x8(%rbp), %xmm1': (5, -1, 1, 8, 0),
    'movntdq %xmm2, 0x8(%rax)': (0, -1, 1, 8, 1),
}
DECLINE = [
    'movupd 0x328(%r14), %xmm2',        # unaligned form never faults
    'movsd 0x8(%rax), %xmm0',           # scalar
    'addsd 0x8(%rax), %xmm0',
    'movq 0x8(%rax), %xmm0',
    'vmovapd 0x8(%rax), %xmm0',         # VEX: alignment is not this path
    'movapd 0x8(%rip), %xmm0',          # RIP-relative
    'movapd %fs:0x8(%rax), %xmm0',      # FS segment
    'movapd 0x8(%rsp), %xmm0',          # RSP base
    'movapd 0x8(%rax,%rax,1), %xmm0',   # base == index
    'movapd %xmm1, %xmm0',              # register operand
    'pmovzxbw 0x8(%rax), %xmm0',        # m64 form
    'invpcid 8(%rax), %rbx',
    'adcx 8(%rax), %rbx',
    'pcmpistri $0, 8(%rcx), %xmm0',
    'pcmpestri $0, 8(%rax), %xmm0',
    'ptest 8(%rax), %xmm0',
    'roundsd $0, 8(%rax), %xmm0',
    'paddd 0x8(%rax), %mm0',            # MMX
]


def text_bytes(obj):
    """The __TEXT,__text section of a thin x86_64 object file."""
    data = obj.read_bytes()
    ncmds = struct.unpack_from('<I', data, 16)[0]
    at = 32
    for _ in range(ncmds):
        cmd, size = struct.unpack_from('<II', data, at)
        if cmd == 0x19:
            for k in range(struct.unpack_from('<I', data, at+64)[0]):
                q = at+72+80*k
                if data[q:q+16].rstrip(b'\0') == b'__text':
                    length, = struct.unpack_from('<Q', data, q+40)
                    offset, = struct.unpack_from('<I', data, q+48)
                    return data[offset:offset+length]
        at += size
    raise ValueError('no __text')


def encode(instructions):
    """Each instruction's bytes (hex), assembled one at a time by clang."""
    out = []
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        for ins in instructions:
            (tmp/'t.s').write_text('.text\n    '+ins+'\n')
            subprocess.run(['xcrun', 'clang', '-arch', 'x86_64', '-c', str(tmp/'t.s'), '-o', str(tmp/'t.o')], check=True)
            out.append(text_bytes(tmp/'t.o').hex())
    return out


class Decode(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.harness = Path(cls.tmp.name)/'harness'
        subprocess.run(['xcrun', 'clang', '-std=c99', '-Wall', '-Wextra', '-Werror', '-o', str(cls.harness),
                        str(HERE/'sse_align_harness.c')], check=True)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def run_harness(self, hexes):
        return subprocess.check_output([str(self.harness), *hexes], text=True).splitlines()

    def test_accepts_aligned_packed_forms(self):
        names = list(ACCEPT)
        codes = encode(names)
        for name, code, line in zip(names, codes, self.run_harness(codes)):
            parts = line.split()
            self.assertEqual(parts[0], 'ok', name)
            self.assertEqual(tuple(int(v) for v in parts[1:6]), ACCEPT[name], name)
            self.assertEqual(int(parts[6]), len(code)//2, name)

    def test_declines_everything_else(self):
        codes = encode(DECLINE)
        for name, line in zip(DECLINE, self.run_harness(codes)):
            self.assertEqual(line, 'no', name)

    def test_truncation_and_invalid_prefixes(self):
        for code in encode(list(ACCEPT)):
            truncated = [code[:i] for i in range(0, len(code), 2)]
            self.assertEqual(self.run_harness(truncated), ['no']*len(truncated), code)
        codes = ['660f38ff4708', '660f3aff470800', 'f0660f284708', '67660f284708',
                 '65660f284708', '48660f284708', '66660f38f64708']
        self.assertEqual(self.run_harness(codes), ['no']*len(codes))

    def test_vectorkit_bytes(self):
        # VectorKit `movapd 0x328(%r14),%xmm2`
        self.assertEqual(self.run_harness(['66410f289628030000']), ['ok 14 -1 1 808 0 9'])


if __name__ == '__main__':
    unittest.main()
