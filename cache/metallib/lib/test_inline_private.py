import ctypes
from pathlib import Path
import subprocess
import tempfile
import unittest
import compatibility
import inline_private


def program(closure=False):
    helper='''define internal <2 x half> @read(<2 x half>* %pointer, i64 %index) #0 {
  %p = getelementptr inbounds <2 x half>, <2 x half>* %pointer, i64 %index
  %v = load <2 x half>, <2 x half>* %p, align 4
  ret <2 x half> %v
}
'''
    call='  %v = call <2 x half> @read(<2 x half>* %base, i64 %index)'
    capture=''
    types=''
    if closure:
        types='%closure = type { [4 x <2 x half>]*, i32 }\n'
        helper='''define internal <2 x half> @read(%closure* %capture, i64 %index) #0 {
  %field = getelementptr inbounds %closure, %closure* %capture, i64 0, i32 0
  %array = load [4 x <2 x half>]*, [4 x <2 x half>]** %field, align 8
  %p = getelementptr inbounds [4 x <2 x half>], [4 x <2 x half>]* %array, i64 0, i64 %index
  %v = load <2 x half>, <2 x half>* %p, align 4
  ret <2 x half> %v
}
'''
        capture='  %capture = alloca %closure, align 8\n  %field = getelementptr inbounds %closure, %closure* %capture, i64 0, i32 0\n  store [4 x <2 x half>]* %a, [4 x <2 x half>]** %field, align 8\n'
        call='  %v = call <2 x half> @read(%closure* %capture, i64 %index)'
    return types+helper+'''define void @probe(i64 %index, i16* %input, i16* %output) #0 {
  %a = alloca [4 x <2 x half>], align 8
  %in = bitcast i16* %input to <2 x half>*
  %value = load <2 x half>, <2 x half>* %in, align 2
  %p = getelementptr inbounds [4 x <2 x half>], [4 x <2 x half>]* %a, i64 0, i64 %index
  store <2 x half> %value, <2 x half>* %p, align 4
  %base = getelementptr inbounds [4 x <2 x half>], [4 x <2 x half>]* %a, i64 0, i64 0
'''+capture+call+'''
  %out = bitcast i16* %output to <2 x half>*
  store <2 x half> %v, <2 x half>* %out, align 2
  ret void
}
attributes #0 = { nounwind }
'''


class InlineTests(unittest.TestCase):
    def test_direct_and_captured_paths_preserve_public_abi_and_eliminate_helpers(self):
        for closure in [False,True]:
            text=program(closure);fixed,report=compatibility.rewrite(text)
            self.assertEqual(inline_private.public_surface(text),inline_private.public_surface(fixed))
            self.assertEqual(report['private_half2_load']['inlining']['functions'],['read'])
            self.assertNotIn('define internal',fixed)
            self.assertEqual(compatibility.rewrite(fixed)[0],fixed)

    def test_contract_checks_ignore_identifier_and_string_spelling(self):
        sources=[program().replace('%index','%noinline'),program().replace('@read(', '@optnone('),program().replace('nounwind }','nounwind "custom"="noinline" }')]
        for text in sources:
            with self.subTest(text=text[:80]):
                fixed,_=compatibility.rewrite(text)
                self.assertEqual(inline_private.public_surface(text),inline_private.public_surface(fixed))

    def test_recursion_escape_and_noinline_refuse(self):
        text=program();recursive=text.replace('  ret <2 x half> %v','  %again = call <2 x half> @read(<2 x half>* %pointer, i64 %index)\n  ret <2 x half> %again')
        escaped=text+'@fp = global <2 x half> (<2 x half>*, i64)* @read\n'
        for source in [recursive,escaped,text.replace('nounwind }','nounwind noinline }')]:
            with self.assertRaises(ValueError):compatibility.rewrite(source)

    def test_quoted_group_numbers_do_not_resolve_attribute_groups(self):
        for value in ('#1', '#999'):
            text = program().replace(') #0 {', ') "custom"="' + value + '" #0 {')
            text += 'attributes #1 = { noinline }\n'
            self.assertEqual(inline_private.selected_functions(text)[2], {'read'})
            fixed, _ = compatibility.rewrite(text)
            self.assertEqual(inline_private.public_surface(text), inline_private.public_surface(fixed))
        for contract in ('noinline', 'optnone'):
            text = program().replace('nounwind }', 'nounwind ' + contract + ' }')
            with self.assertRaisesRegex(ValueError, 'contract'):
                inline_private.selected_functions(text)

    def test_comments_and_quoted_semicolons_are_not_contracts(self):
        for comment in ('noinline', '#999'):
            text = program().replace(') #0 {', ') #0 { ; ' + comment)
            self.assertEqual(inline_private.selected_functions(text)[2], {'read'})
            fixed, _ = compatibility.rewrite(text)
            self.assertEqual(inline_private.public_surface(text), inline_private.public_surface(fixed))
        text = program().replace(') #0 {', ') "custom"="; noinline #999" #0 {')
        fixed, _ = compatibility.rewrite(text)
        self.assertEqual(inline_private.public_surface(text), inline_private.public_surface(fixed))
        text = program().replace(') #0 {', ') #0 { ; harmless').replace('nounwind }', 'nounwind noinline }')
        with self.assertRaisesRegex(ValueError, 'contract'):
            inline_private.selected_functions(text)

    def test_shared_helper_multiple_public_callers(self):
        source=program();start=source.index('define void @probe');end=source.index('attributes #0')
        source=source[:end]+source[start:end].replace('@probe(', '@other(')+source[end:]
        fixed,_=compatibility.rewrite(source)
        self.assertEqual(set(inline_private.public_surface(fixed)), {'probe','other'})
        self.assertEqual(inline_private.public_surface(source),inline_private.public_surface(fixed))

    def test_dynamic_scalar_half_exposure_refuses(self):
        source=program().replace('  ret <2 x half> %v', '  %scalar = bitcast <2 x half>* %pointer to half*\n  %q = getelementptr inbounds half, half* %scalar, i64 %index\n  %h = load half, half* %q, align 2\n  %mixed = insertelement <2 x half> %v, half %h, i32 0\n  ret <2 x half> %mixed')
        with self.assertRaisesRegex(ValueError, 'scalar-half'):
            compatibility.rewrite(source)

    def test_unknown_capture_escape_stays_refusing(self):
        text=program(True).replace('  %v = call <2 x half> @read(%closure* %capture, i64 %index)','  call void @escape(%closure* %capture)\n  %v = call <2 x half> @read(%closure* %capture, i64 %index)')
        text='declare void @escape(%closure*)\n'+text
        with self.assertRaises(ValueError):compatibility.rewrite(text)

    def test_native_cpu_direct_and_closure_all_half_bits_collective_indices(self):
        with tempfile.TemporaryDirectory(prefix='shader-inline-semantics-')as work:
            work=Path(work)
            for closure in [False,True]:
                text=program(closure);fixed,_=compatibility.rewrite(text)
                for name,ir in [('original',text),('fixed',fixed)]:
                    ll=work/(str(closure)+'-'+name+'.ll');ll.write_text(ir);lib=ll.with_suffix('.dylib')
                    subprocess.run(['xcrun','clang','-O2','-dynamiclib',str(ll),'-o',str(lib)],capture_output=True,check=True)
                    handle=ctypes.CDLL(str(lib));fn=handle.probe;fn.argtypes=[ctypes.c_uint64,ctypes.POINTER(ctypes.c_uint16),ctypes.POINTER(ctypes.c_uint16)]
                    for bits in range(65536):
                        values=(ctypes.c_uint16*2)(bits,65535-bits);out=(ctypes.c_uint16*2)();fn(bits%4,values,out);self.assertEqual(tuple(out),tuple(values))
