import ctypes
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
import null_compare
from test_null_compare import module


class ParserRegressionTests(unittest.TestCase):
    def test_only_real_attribute_tokens_prove_nonnull(self):
        for arg in ['i32 addrspace(1)* %nonnull', 'i32 addrspace(1)* %nonnull.foo',
                    '%nonnull addrspace(1)* %p', '%"nonnull" addrspace(1)* %p',
                    'i32 addrspace(1)* "custom"="nonnull" %p',
                    'i32 addrspace(1)* dereferenceable_or_null(4) %p',
                    'i32 addrspace(1)* dereferenceable(0) %p']:
            with self.subTest(arg=arg): self.assertFalse(null_compare.parameter_nonnull(arg))
        for attr in ['nonnull', 'dereferenceable(4)']:
            self.assertTrue(null_compare.parameter_nonnull(f'i32 addrspace(1)* {attr} %p'))

    def test_only_actual_load_operand_is_a_dereference(self):
        for extra in ['  ; load i32, i32 addrspace(1)* %p',
                      '  %load = add i32 0, 1 ; load i32, i32 addrspace(1)* %p',
                      '  %x = call i32 @load() ; load i32, i32 addrspace(1)* %p']:
            text = module(extra + '\n  %c = icmp eq i32 addrspace(1)* %p, null\n  ret i1 %c')
            fixed, _ = null_compare.rewrite(text)
            self.assertIn('store volatile i32 addrspace(1)* %p', fixed)
        text = module('  %x = load i32, i32 addrspace(1)* %p ; load i32, i32 addrspace(1)* %q\n  ret i1 true', 'i32 addrspace(1)* %p, i32 addrspace(1)* %q')
        functions, _ = null_compare.read_functions(text.splitlines())
        self.assertEqual(functions['f']['entry_dereferences'], {'%p'})
        self.assertEqual(null_compare.without_comment('  %x = load i32, %"type;name"* %p ; comment'), '  %x = load i32, %"type;name"* %p ')

    def test_quoted_named_pointer_uses_bit_preserving_spill(self):
        for name in ['struct.AGXGPU::__intersection_function_table_t', 'nonnull;type']:
            ty = '%"' + name + '"'
            text = ty + ' = type opaque\n' + module('  %c = icmp eq ' + ty + ' addrspace(1)* %p, null\n  ret i1 %c', ty + ' addrspace(1)* %p')
            fixed, _ = null_compare.rewrite(text)
            self.assertIn('alloca ' + ty + ' addrspace(1)*', fixed)
            self.assertIn('icmp ne i64', fixed)
            self.assertEqual(null_compare.scan(fixed), [])

    def test_review_reproductions_and_misleading_names_on_native_cpu(self):
        cases=[]
        for name in ['nonnull', 'nonnull.foo', 'dereferenceable', 'load', 'ordinary']:
            cases.append(module(f'  %c = icmp eq i32 addrspace(1)* %{name}, null\n  %r = zext i1 %c to i32\n  ret i32 %r', f'i32 addrspace(1)* %{name}').replace('define i1','define i32'))
        for prefix in ['', 'entry:\n']:
            cases.append(module(prefix + '  ; load i32, i32 addrspace(1)* %p\n  %fake = add i32 0, 1 ; load i32, i32 addrspace(1)* %p\n  %c = icmp eq i32 addrspace(1)* %p, null\n  %r = zext i1 %c to i32\n  ret i32 %r').replace('define i1','define i32'))
        with tempfile.TemporaryDirectory(prefix='shader-null-review-') as work:
            work=Path(work)
            for index,text in enumerate(cases):
                fixed,_=null_compare.rewrite(text)
                self.assertIn('store volatile',fixed)
                for kind,ir in [('original',text),('fixed',fixed)]:
                    ll=work/f'{index}-{kind}.ll';ll.write_text(ir);lib=ll.with_suffix('.dylib')
                    subprocess.run(['xcrun','clang','-O2','-dynamiclib',str(ll),'-o',str(lib)],capture_output=True,check=True)
                    handle=ctypes.CDLL(str(lib));fn=handle.f;fn.argtypes=[ctypes.c_void_p];fn.restype=ctypes.c_int
                    for bits in [0,1,1<<32,1<<63,(1<<64)-1]:self.assertEqual(fn(bits),int(bits==0))
