import ctypes
import random
from pathlib import Path
import subprocess
import tempfile
import unittest
import private_half2
import compatibility
from test_half2_load import module


class PrivateTests(unittest.TestCase):
    def test_mutation_nested_aliases_and_multiple_arrays(self):
        text = module('  store <2 x half> <half 0xH8000, half 0xH7E01>, <2 x half>* %p0, align 4')
        text = text.replace('  %q =', '  %alias = getelementptr inbounds [4 x <2 x half>], [4 x <2 x half>]* %a, i64 0, i64 0\n  %q =')
        fixed, report = private_half2.rewrite(text)
        self.assertEqual(report['arrays'], 1)
        self.assertIn('zext <2 x i16>', fixed)
        self.assertIn('load volatile <2 x i32>', fixed)
        self.assertEqual(compatibility.scan(fixed)['private_half2_load'], [])
        other = '\n'.join(line.replace('%a', '%b').replace('%p', '%r').replace('%q', '%s').replace('%v', '%w').replace('%alias', '%alias2') for line in text.splitlines()[1:-2])
        double = text.replace('  ret ', other + '\n  ret ')
        fixed, report = private_half2.rewrite(double)
        self.assertEqual(report['arrays'], 2)
        names = [line.split(' = ')[0] for line in fixed.splitlines() if ' = ' in line]
        self.assertEqual(len(names), len(set(names)))
        self.assertEqual(private_half2.rewrite(fixed)[0], fixed)

    def test_lifetime_sizes_and_collisions(self):
        text = module('  %compat.wide.v.bits = add i32 1, 2\n  %life = bitcast [4 x <2 x half>]* %a to i8*\n  call void @llvm.lifetime.start.p0i8(i64 16, i8* %life)\n  call void @llvm.lifetime.end.p0i8(i64 -1, i8* %life)')
        fixed, report = private_half2.rewrite(text)
        self.assertEqual(report['arrays'], 1)
        self.assertIn('i64 32, i8* %life', fixed)
        self.assertIn('i64 -1, i8* %life', fixed)
        self.assertIn('%compat.wide.v.bits.1 = load', fixed)

    def test_escape_unknown_uses_and_alias_stores_refuse(self):
        for extra in ('  call void @escape([4 x <2 x half>]* %a)',
                      '  %bad = ptrtoint [4 x <2 x half>]* %a to i64',
                      '  store <2 x half>* %p0, <2 x half>** %escape, align 8',
                      '  %life = bitcast [4 x <2 x half>]* %a to i8*\n  call void @escape8(i8* %life)'):
            text = module(extra)
            with self.subTest(extra=extra):
                self.assertEqual(private_half2.rewrite(text)[0], text)
                with self.assertRaises(ValueError):
                    compatibility.rewrite(text)

    def test_volatile_load_is_scanned_and_not_elided(self):
        text = module().replace(' = load <2 x half>', ' = load volatile <2 x half>')
        self.assertTrue(compatibility.scan(text)['private_half2_load'])
        fixed, _ = compatibility.rewrite(text)
        self.assertIn('load volatile <2 x i32>', fixed)

    def test_native_cpu_semantics_all_half_bit_patterns(self):
        # Independent execution oracle: LLVM's native CPU code generator runs
        # the original and lowered private-memory programs on every half bit pattern.
        text = '''define void @probe(i64 %index, i16* %input, i16* %output) {
  %a = alloca [4 x <2 x half>], align 8
  %in = bitcast i16* %input to <2 x half>*
  %value = load <2 x half>, <2 x half>* %in, align 2
  %p = getelementptr inbounds [4 x <2 x half>], [4 x <2 x half>]* %a, i64 0, i64 %index
  store <2 x half> %value, <2 x half>* %p, align 4
  %loaded = load <2 x half>, <2 x half>* %p, align 4
  %out = bitcast i16* %output to <2 x half>*
  store <2 x half> %loaded, <2 x half>* %out, align 2
  ret void
}
'''
        fixed, report = private_half2.rewrite(text)
        self.assertEqual(report['arrays'], 1)
        with tempfile.TemporaryDirectory(prefix='shader-semantics-') as work:
            work = Path(work)
            funcs, handles = [], []
            for name, ir in [('original', text), ('fixed', fixed)]:
                path = work / (name + '.ll'); path.write_text(ir)
                lib = work / (name + '.dylib')
                subprocess.run(['xcrun', 'clang', '-O2', '-dynamiclib', str(path), '-o', str(lib)], capture_output=True, check=True)
                handle = ctypes.CDLL(str(lib)); handles.append(handle)
                fn = handle.probe
                fn.argtypes = [ctypes.c_uint64, ctypes.POINTER(ctypes.c_uint16), ctypes.POINTER(ctypes.c_uint16)]
                funcs.append(fn)
            rng = random.Random(921)
            for bits in range(65536):
                values = (ctypes.c_uint16 * 2)(bits, rng.randrange(65536))
                expected = tuple(values)
                for fn in funcs:
                    out = (ctypes.c_uint16 * 2)()
                    fn(bits % 4, values, out)
                    self.assertEqual(tuple(out), expected)
