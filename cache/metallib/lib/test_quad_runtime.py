"""Run the shipped helper IR on the CPU with modeled Intel lane intrinsics."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import quad_runtime


@unittest.skipUnless(shutil.which('xcrun'), 'needs Xcode clang')
class QuadSemantics(unittest.TestCase):
    def test_actual_ir_preserves_bits_and_quad_boundaries(self):
        with tempfile.TemporaryDirectory(prefix='quad-semantics-') as temporary:
            work = Path(temporary)
            sources = []
            for source in sorted(quad_runtime.SOURCES.glob('*.ll')):
                text = source.read_text().replace('llvm.igil.get.simd.id', 'test_lane')
                text = text.replace('llvm.igil.simd.shuffle', 'test_shuffle')
                # The mocks read the lane model; only GPU intrinsics are readnone.
                text = text.replace(' readnone', '')
                if source.stem == 'xor-v2f16':
                    # AArch64's C ABI coerces half2 to i32, unlike a direct LLVM
                    # vector call. Cross the test boundary through memory.
                    text += '''
define void @test_quad_xor_half2(i16* %input, i16 %selector, i16* %output) {
  %src = bitcast i16* %input to <2 x half>*
  %v = load <2 x half>, <2 x half>* %src, align 2
  %r = call <2 x half> @air_quad_shuffle_xor_v2f16(<2 x half> %v, i16 %selector)
  %dst = bitcast i16* %output to <2 x half>*
  store <2 x half> %r, <2 x half>* %dst, align 2
  ret void
}
'''
                path = work / source.name
                path.write_text(text)
                sources.append(str(path))
            binary = work / 'semantic-test'
            subprocess.run(['xcrun', 'clang', '-O2', '-Wno-override-module',
                            *sources, str(quad_runtime.SOURCES / 'semantic-test.c'),
                            '-o', str(binary)], check=True, capture_output=True)
            subprocess.run(['codesign', '-s', '-', str(binary)], check=True, capture_output=True)
            result = subprocess.run([str(binary)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('bit comparisons passed', result.stdout)
            print(result.stdout.strip())


if __name__ == '__main__':
    unittest.main()
