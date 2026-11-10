import re
import ctypes
import subprocess
import tempfile
from pathlib import Path
import unittest
import null_compare as fix
from memory_effects import relax_private_volatile, strip_restrictions


def module(body, args='i32 addrspace(1)* %p', attrs=''):
    return ('target datalayout = "e-p:64:64"\n'
            f'define i1 @f({args}) {attrs} {{\n' + body + '\n}\n')


class NullTests(unittest.TestCase):
    def test_nullable_public_abi_and_bits(self):
        for pred in ('eq', 'ne'):
            text = module(f'  %c = icmp {pred} i32 addrspace(1)* %p, null\n  ret i1 %c')
            result, report = fix.rewrite(text)
            self.assertEqual(result.splitlines()[1], text.splitlines()[1])
            self.assertEqual(report['compares'], 1)
            self.assertEqual(fix.scan(result), [])
            self.assertIn('store volatile i32 addrspace(1)* %p', result)
            self.assertEqual(result.count('load volatile i64'), 1)
            self.assertNotIn('ptrtoint', result)
            for bits in (0, 1, 1 << 32, (1 << 64) - 1):
                valid = bits != 0
                inverted = 'true' in next(x for x in result.splitlines() if '%c = xor' in x)
                self.assertEqual(valid ^ inverted, (bits == 0) if pred == 'eq' else (bits != 0))
            self.assertEqual(fix.rewrite(result)[0], result)

    def test_native_cpu_null_and_nonnull_semantics(self):
        text = module('  %c = icmp eq i32 addrspace(1)* %p, null\n  %r = zext i1 %c to i32\n  ret i32 %r').replace('define i1', 'define i32')
        fixed, _ = fix.rewrite(text)
        with tempfile.TemporaryDirectory(prefix='shader-null-semantics-') as work:
            work = Path(work)
            for name, ir in [('original', text), ('fixed', fixed)]:
                ll = work / (name + '.ll'); ll.write_text(ir)
                lib = work / (name + '.dylib')
                subprocess.run(['xcrun', 'clang', '-O2', '-dynamiclib', str(ll), '-o', str(lib)], check=True, capture_output=True)
                handle = ctypes.CDLL(str(lib)); fn = handle.f
                fn.argtypes = [ctypes.c_void_p]; fn.restype = ctypes.c_uint32
                for bits in [0, 1, 4, 1 << 32, 1 << 63, (1 << 64) - 1]:
                    self.assertEqual(fn(bits), int(bits == 0))

    def test_entry_label_and_ssa_collision(self):
        result, _ = fix.rewrite(module('entry:\n  %valid.slot.p = add i32 0, 1\n  %c = icmp eq i32 addrspace(1)* null, %p\n  ret i1 %c'))
        self.assertLess(result.index('entry:'), result.index('alloca'))
        self.assertIn('%valid.slot.p.1 = alloca', result)

    def test_loaded_pointer_one_application_read(self):
        text = module('  %p = load i32 addrspace(1)*, i32 addrspace(1)* addrspace(1)* %table, align 8\n  %c = icmp ne i32 addrspace(1)* %p, null\n  ret i1 %c', 'i32 addrspace(1)* addrspace(1)* %table')
        result, _ = fix.rewrite(text)
        self.assertEqual(len(re.findall(r' = load ', result)), 1)
        self.assertIn('load i64, i64 addrspace(1)*', result)
        self.assertIn('%p = inttoptr i64', result)

    def test_vector_pointer_spill_and_phi_select(self):
        text = module('  %q = select i1 %choose, <2 x float> addrspace(1)* %p, <2 x float> addrspace(1)* null\n  %c = icmp ne <2 x float> addrspace(1)* %q, null\n  ret i1 %c', '<2 x float> addrspace(1)* %p, i1 %choose')
        fixed, report = fix.rewrite(text)
        self.assertIn('alloca <2 x float> addrspace(1)*', fixed)
        self.assertEqual(report['selects'], 1)
        self.assertEqual(fix.scan(fixed), [])

    def test_internal_parameter_threads_without_public_abi_change(self):
        text = module('  %r = call i1 @helper(i32 addrspace(1)* %p)\n  ret i1 %r')
        text += 'define internal i1 @helper(i32 addrspace(1)* %x) {\n  %c = icmp eq i32 addrspace(1)* %x, null\n  ret i1 %c\n}\n'
        fixed, report = fix.rewrite(text)
        self.assertIn('define i1 @f(i32 addrspace(1)* %p)', fixed)
        self.assertIn('define internal i1 @helper(i32 addrspace(1)* %x, i1 %valid)', fixed)
        self.assertEqual(report['threaded_parameters'], 1)
        self.assertEqual(report['calls'], 1)

    def test_threading_refuses_function_pointer_escape(self):
        text = module('  %r = call i1 @helper(i32 addrspace(1)* %p)\n  ret i1 %r')
        text += 'define internal i1 @helper(i32 addrspace(1)* %x) {\n  %c = icmp eq i32 addrspace(1)* %x, null\n  ret i1 %c\n}\n@fp = global i1 (i32 addrspace(1)*)* @helper\n'
        with self.assertRaisesRegex(ValueError, 'indirect reference'):
            fix.rewrite(text)

    def test_integer_width_mismatch_refuses(self):
        with self.assertRaisesRegex(ValueError, 'width'):
            fix.rewrite(module('  %p = inttoptr i128 %bits to i32 addrspace(1)*\n  %c = icmp eq i32 addrspace(1)* %p, null\n  ret i1 %c', 'i128 %bits'))

    def test_external_return_spills_after_call(self):
        text = module('  %p = call i8 addrspace(1)* @external()\n  %c = icmp eq i8 addrspace(1)* %p, null\n  ret i1 %c', '')
        result, _ = fix.rewrite(text)
        self.assertLess(result.index('call i8'), result.index('store volatile'))

    def test_unknown_origin_and_non64_refuse(self):
        with self.assertRaises(ValueError):
            fix.rewrite(module('  %p = freeze i32 addrspace(1)* %x\n  %c = icmp eq i32 addrspace(1)* %p, null\n  ret i1 %c', 'i32 addrspace(1)* %x'))
        with self.assertRaisesRegex(ValueError, '64-bit'):
            fix.rewrite(module('  %c = icmp eq i32 addrspace(1)* %p, null\n  ret i1 %c').replace('p:64:64', 'p:32:32'))

    def test_quoted_attributes_are_preserved(self):
        self.assertEqual(strip_restrictions('readonly "custom"="readonly" nounwind'), ' "custom"="readonly" nounwind')

    def test_attrs_cloned_and_propagated_without_changing_parameters(self):
        text = module('  %c = icmp eq i32 addrspace(1)* %p, null\n  ret i1 %c', 'i32 addrspace(1)* readonly %p', '#0')
        text += 'define i1 @caller(i32 addrspace(1)* %x) #0 {\n  %r = call i1 @f(i32 addrspace(1)* %x) #0\n  ret i1 %r\n}\ndefine void @safe() #0 {\n  ret void\n}\nattributes #0 = { readnone readonly argmemonly nosync nounwind }\n'
        result, _ = fix.rewrite(text)
        self.assertIn('i32 addrspace(1)* readonly %p', result)
        self.assertIn('define void @safe() #0', result)
        self.assertIn('attributes #0 = { readnone readonly argmemonly nosync nounwind }', result)
        self.assertEqual(result.count('#1'), 4)
        self.assertIn('nounwind', result.split('attributes #1 =')[1])
        self.assertNotRegex(result.split('attributes #1 =')[1], r'readnone|readonly|argmemonly|nosync')

    def test_escaping_internal_function_refuses_attr_change(self):
        text = module('  ret i1 true') + '@fp = global i1 (i32 addrspace(1)*)* @f\n'
        with self.assertRaisesRegex(ValueError, 'indirect reference'):
            relax_private_volatile(text, {'f'})
