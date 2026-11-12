"""Host tests for the IGIL rewrite (src/compiler/igil_rewrite.c) on IGIL captured from the HD 4000 compiler.

Fixtures are the IGIL text the IGIL target (libIGIL-Metal) produced for this tool's own kernels (kernels/*.metal),
captured with harness/dbg.py. Run: python3.14 -I -B -m unittest discover -s tools/metal-argbuf/tests
"""
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE.parent / 'src'
FIX = HERE / 'fixtures'


def config():
    text = (SRC / 'argbuf_config.h').read_text()
    return {k: int(v) for k, v in re.findall(r'#define AGB_(\w+)\s+(\d+)', text)}


class Rewrite(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.tool = Path(cls.tmp.name) / 'igil_tool'
        subprocess.run(['clang', '-O1', '-Wall', '-Wextra', '-Werror', str(SRC / 'compiler/igil_rewrite.c'),
                        str(SRC / 'compiler/igil_tool.c'), '-o', str(cls.tool)], check=True)
        cls.cfg = config()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def rewrite(self, text):
        src = Path(self.tmp.name) / 'in.igil'
        dst = Path(self.tmp.name) / 'out.igil'
        src.write_text(text)
        r = subprocess.run([str(self.tool), str(src), str(dst)], capture_output=True, text=True)
        self.assertIn(r.returncode, (0, 1), r.stderr)
        return r.returncode == 0, dst.read_text(), r.stdout

    def fixture(self, name):
        return self.rewrite((FIX / name).read_text())

    def locations(self, text, kind):
        return [int(m) for m in re.findall(kind + r' \w+.*?=\s*\{[^}]*?LOCATION_INDEX, (\d+), 0', text, re.S)]

    def test_texture_and_pointer(self):
        # t1: texture member read + device pointer member (probe4 t1)
        changed, out, _ = self.fixture('t1.igil')
        self.assertTrue(changed)
        self.assertNotIn('LD_UAV_TYPED', out)
        self.assertRegex(out, r'LOAD r\d+, r\d+, t\[r\d+\.x\], 2D;')
        c = self.cfg
        self.assertEqual(self.locations(out, 'DCL_RESOURCE'), list(range(c['TEX_SLOT'], c['TEX_SLOT'] + c['TEX_COUNT'])))
        self.assertEqual(self.locations(out, 'DCL_UAVRAW')[-c['BUF_COUNT']:],
                         list(range(c['BUF_SLOT'], c['BUF_SLOT'] + c['BUF_COUNT'])))

    def test_fixed_binding_table_index(self):
        # binding table = constant buffers, textures, UAVs by register: the first added UAV must sit at BUF_BTI
        for name in ('t1.igil', 't5.igil', 'kab.igil'):
            changed, out, _ = self.fixture(name)
            self.assertTrue(changed, name)
            ncb = out.count('DCL_CONSTANTBUFFER C')
            ntex = out.count('DCL_RESOURCE t')
            first = int(re.search(r'DCL_UAVRAW u(\d+), TRUE =\s*\{\s*KERNEL_ARGUMENT, \d+,\s*LOCATION_INDEX, '
                                  + str(self.cfg['BUF_SLOT']) + ', 0', out).group(1))
            self.assertEqual(ncb + ntex + first, self.cfg['BUF_BTI'], name)

    def test_constant_pointer_retargeted(self):
        # kab: `constant` pointers loaded from the argument buffer are dereferenced through C0, which ignores the
        # pointer's high word; the rewrite moves every such access to the fixed UAV range
        changed, out, _ = self.fixture('kab.igil')
        self.assertTrue(changed)
        src = (FIX / 'kab.igil').read_text()
        # first-level loads from the argument buffer itself stay on C0
        self.assertIn('LD_RAW_PTR r26, r25, C0;', out)
        # loads through a pointer that came from the argument buffer move off C0
        for before, after in (('LD_RAW_PTR r27, r26, C0;', 'LD_RAW_PTR r27, r26, u'),
                              ('LD_RAW_PTR r67, r66, C0;', 'LD_RAW_PTR r67, r66, u')):
            self.assertIn(before, src)
            self.assertNotIn(before, out)
            self.assertIn(after, out)

    def samplers(self, text):
        return [(int(a), int(b)) for a, b in re.findall(r'DCL_SAMPLER s(\d+) =\s*\{\s*KERNEL_ARGUMENT, \d+,\s*LOCATION_INDEX, (\d+), 0', text)]

    def test_sampler_range(self):
        # ks: texture + sampler members; both handles are offset into declared ranges
        changed, out, _ = self.fixture('ks.igil')
        self.assertTrue(changed)
        c = self.cfg
        self.assertEqual(self.samplers(out), [(k, c['SAMP_SLOT'] + k) for k in range(c['SAMP_COUNT'])])
        self.assertEqual(len(re.findall(r'SAMPLE_L .*t\[r\d+\.x\], s0\[r\d+\.x\]', out)), 2)

    def test_sampler_range_after_own_sampler(self):
        # c7 compiled with the IGILTextureTypeEncode wrapper: the function's own sampler is s0, so the argument-buffer
        # range starts at s1 and the handle is offset by 1
        src = (FIX / 'k13-c7-kept.igil').read_text()
        self.assertRegex(src, r'SAMPLE_L .*s0\[r\d+\.x\]')
        changed, out, _ = self.fixture('k13-c7-kept.igil')
        self.assertTrue(changed)
        c = self.cfg
        self.assertEqual(self.samplers(out)[1:], [(1 + k, c['SAMP_SLOT'] + k) for k in range(c['SAMP_COUNT'])])
        self.assertRegex(out, r'IADD r(\d+)\.x, r\d+\.x, 1;\n\tSAMPLE_L .*s0\[r\1\.x\]')
        self.assertRegex(out, r'SAMPLE_L .*t0, s0, ')   # the function's own sample is untouched

    def test_dropped_sample_documented(self):
        # c6/c3 compiled WITHOUT the wrapper: IGILTextureTypeEncode drops the argument-buffer sample
        for name in ('k13-c6.igil', 'k13-c3.igil'):
            self.assertNotRegex((FIX / name).read_text(), r'SAMPLE_L .*s0\[', name)
        self.assertRegex((FIX / 'k13-c8.igil').read_text(), r'SAMPLE_L .*s0\[')

    def test_plain_program_unchanged(self):
        plain = ('\tPROGRAM\n\tSHADER k\n\n\tVERSION_3_0\n\n\tDCL_UAVRAW u0, TRUE =\n\t{\n\t  KERNEL_ARGUMENT, 0,\n'
                 '\t  LOCATION_INDEX, 0, 0,\n\t  EMULATION_ARGUMENT, FALSE,\n\t  STRIDE, 16, 16\n\t};\n\n'
                 '\tDCL_TEMPS r0..r3;\n\tADDRESS_OF r0, u0;\n\tLD_RAW_PTR r1, r0, u0;\n\tSTORE_RAW_PTR u0, r0, r1;\n\tRET;\n\n\tEND\n')
        changed, out, _ = self.rewrite(plain)
        self.assertFalse(changed)
        self.assertEqual(out, plain)


if __name__ == '__main__':
    unittest.main()
