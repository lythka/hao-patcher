import ctypes
from pathlib import Path
import subprocess
import tempfile
import unittest
import compatibility
import private_half2


def program(call=None):
    call=call or '  call void @llvm.memset.p0i8.i64(i8* noundef nonnull align 4 dereferenceable(64) %bytes, i8 0, i64 64, i1 false)'
    return '''declare void @llvm.memset.p0i8.i64(i8*, i8, i64, i1)
define void @probe(i64 %index, i16* %input, i16* %output) {
  %a = alloca [16 x <2 x half>], align 4
  %bytes = bitcast [16 x <2 x half>]* %a to i8*
  %in = bitcast i16* %input to <2 x half>*
  %value = load <2 x half>, <2 x half>* %in, align 2
  %p = getelementptr inbounds [16 x <2 x half>], [16 x <2 x half>]* %a, i64 0, i64 %index
  store <2 x half> %value, <2 x half>* %p, align 4
'''+call+'''
  %loaded = load <2 x half>, <2 x half>* %p, align 4
  %out = bitcast i16* %output to <2 x half>*
  store <2 x half> %loaded, <2 x half>* %out, align 2
  ret void
}
'''


class ZeroTests(unittest.TestCase):
    def test_whole_zero_resizes_extent_and_preserves_repeated_calls(self):
        text=program();call=next(x for x in text.splitlines()if x.startswith('  call'))
        text=text.replace(call,call+'\n'+call)
        fixed,report=private_half2.rewrite(text)
        self.assertEqual(report['arrays'],1);self.assertEqual(fixed.count('i64 128, i1 false'),2)
        self.assertEqual(fixed.count('dereferenceable(128)'),2)
        self.assertEqual(private_half2.rewrite(fixed)[0],fixed)
        self.assertEqual(compatibility.scan(fixed)['private_half2_load'],[])

    def test_partial_nonzero_volatile_offset_copy_and_escape_refuse(self):
        original=program();call=next(x for x in original.splitlines()if x.startswith('  call'))
        bad=[call.replace('i64 64','i64 63'),call.replace('i64 64','i64 65'),call.replace('i8 0','i8 1'),call.replace('i1 false','i1 true'),call.replace('align 4','align 8'),call.replace('dereferenceable(64)','dereferenceable(32)'),
             '  %offset = getelementptr inbounds i8, i8* %bytes, i64 1\n'+call.replace('%bytes','%offset'),
             '  call void @llvm.memcpy.p0i8.p0i8.i64(i8* %bytes, i8* %bytes, i64 64, i1 false)',
             '  call void @llvm.memmove.p0i8.p0i8.i64(i8* %bytes, i8* %bytes, i64 64, i1 false)',
             call+'\n  call void @escape(i8* %bytes)']
        for replacement in bad:
            text=original.replace(call,replacement)
            with self.subTest(call=replacement):
                self.assertEqual(private_half2.rewrite(text)[0],text)
                with self.assertRaises(ValueError):compatibility.rewrite(text)

    def test_native_cpu_zeroes_all_indices_after_half_bit_writes(self):
        original=program()
        call=next(x for x in original.splitlines()if x.startswith('  call'))
        original=original.replace(call, '  %before = load <2 x half>, <2 x half>* %p, align 4\n  %first = bitcast i16* %output to <2 x half>*\n  store <2 x half> %before, <2 x half>* %first, align 2\n'+call)
        original=original.replace('  %out = bitcast i16* %output', '  %middle = getelementptr inbounds i16, i16* %output, i64 2\n  %out = bitcast i16* %middle')
        original=original.replace('  ret void', '  store <2 x half> %value, <2 x half>* %p, align 4\n  %after = load <2 x half>, <2 x half>* %p, align 4\n  %last = getelementptr inbounds i16, i16* %output, i64 4\n  %lastpair = bitcast i16* %last to <2 x half>*\n  store <2 x half> %after, <2 x half>* %lastpair, align 2\n  ret void')
        fixed,_=private_half2.rewrite(original)
        with tempfile.TemporaryDirectory(prefix='shader-private-zero-')as work:
            work=Path(work)
            for name,text in [('original',original),('fixed',fixed)]:
                ll=work/(name+'.ll');ll.write_text(text);lib=work/(name+'.dylib')
                subprocess.run(['xcrun','clang','-O2','-dynamiclib',str(ll),'-o',str(lib)],capture_output=True,check=True)
                handle=ctypes.CDLL(str(lib));fn=handle.probe;fn.argtypes=[ctypes.c_uint64,ctypes.POINTER(ctypes.c_uint16),ctypes.POINTER(ctypes.c_uint16)]
                for bits in range(65536):
                    values=(ctypes.c_uint16*2)(bits,65535-bits);out=(ctypes.c_uint16*6)();fn(bits%16,values,out);self.assertEqual(tuple(out),(bits,65535-bits,0,0,bits,65535-bits))
