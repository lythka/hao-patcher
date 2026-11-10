import ctypes
from pathlib import Path
import subprocess
import tempfile
import unittest
import compatibility
import private_half2


def program():
    return '''%matrix = type { [4 x <2 x half>] }
define void @probe(i64 %index, i16* %input, i16* %output) {
  %a = alloca %matrix, align 4
  %in = bitcast i16* %input to <2 x half>*
  %value = load <2 x half>, <2 x half>* %in, align 2
  %p = getelementptr inbounds %matrix, %matrix* %a, i64 0, i32 0, i64 %index
  store <2 x half> %value, <2 x half>* %p, align 4
  %loaded = load <2 x half>, <2 x half>* %p, align 4
  %out = bitcast i16* %output to <2 x half>*
  store <2 x half> %loaded, <2 x half>* %out, align 2
  ret void
}
'''


class MatrixTests(unittest.TestCase):
    def test_local_only_flattening_preserves_named_type_and_public_abi(self):
        text=program()+'define void @untouched(%matrix* %p) {\n  ret void\n}\n'
        fixed,report=private_half2.rewrite(text)
        self.assertEqual(report['matrices'],1)
        self.assertIn('%matrix = type { [4 x <2 x half>] }',fixed)
        self.assertIn('define void @untouched(%matrix* %p)',fixed)
        self.assertIn('%a = alloca [4 x <2 x i32>], align 4',fixed)
        self.assertIn('getelementptr inbounds [4 x <2 x i32>], [4 x <2 x i32>]* %a, i64 0, i64 %index',fixed)
        self.assertEqual(compatibility.scan(fixed)['private_half2_load'],[])
        self.assertEqual(private_half2.rewrite(fixed)[0],fixed)

    def test_struct_escape_copy_packing_and_other_fields_refuse(self):
        original=program()
        cases=[original.replace('{ [4 x <2 x half>] }','{ [4 x <2 x half>], i32 }'),
               original.replace('{ [4 x <2 x half>] }','<{ [4 x <2 x half>] }>'),
               original.replace('{ [4 x <2 x half>] }','{ [4 x <2 x half>], %matrix* }'),
               original.replace('  ret void','  call void @escape(%matrix* %a)\n  ret void'),
               original.replace('  ret void','  %copy = load %matrix, %matrix* %a, align 4\n  ret void')]
        for text in cases:
            with self.subTest(text=text[:80]):
                self.assertEqual(private_half2.rewrite(text)[0],text)
                with self.assertRaises(ValueError):compatibility.rewrite(text)

    def test_lifetime_and_independent_roots(self):
        text=program().replace('  %in =','  %life = bitcast %matrix* %a to i8*\n  call void @llvm.lifetime.start.p0i8(i64 16, i8* %life)\n  %in =')
        fixed,report=private_half2.rewrite(text)
        self.assertEqual(report['matrices'],1);self.assertIn('i64 32, i8* %life',fixed)
        self.assertIn('bitcast [4 x <2 x i32>]* %a to i8*',fixed)

    def test_native_cpu_all_half_bits_and_collective_indices(self):
        original=program();fixed,_=private_half2.rewrite(original)
        with tempfile.TemporaryDirectory(prefix='shader-matrix-semantics-')as work:
            work=Path(work)
            for name,ir in [('original',original),('fixed',fixed)]:
                ll=work/(name+'.ll');ll.write_text(ir);lib=work/(name+'.dylib')
                subprocess.run(['xcrun','clang','-O2','-dynamiclib',str(ll),'-o',str(lib)],capture_output=True,check=True)
                handle=ctypes.CDLL(str(lib));fn=handle.probe;fn.argtypes=[ctypes.c_uint64,ctypes.POINTER(ctypes.c_uint16),ctypes.POINTER(ctypes.c_uint16)]
                for bits in range(65536):
                    values=(ctypes.c_uint16*2)(bits,65535-bits);out=(ctypes.c_uint16*2)();fn(bits%4,values,out);self.assertEqual(tuple(out),tuple(values))
