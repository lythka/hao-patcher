"""Host tests for patches/metalold-selectors/build: build the shim, load it into an x86_64 process with stand-in legacy
classes (tests/fake_stack.m) and check every answer; check the Mach-O; check the kill switch. Needs Xcode's clang
and Rosetta (the shim and the stand-ins are x86_64 like the image's Metal clients); skips without them."""
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

HERE = pathlib.Path(__file__).resolve().parent
UNIT = HERE.parent
MAKE = UNIT / 'make'


def rosetta():
    try:
        return subprocess.run(['arch', '-x86_64', '/usr/bin/true'], capture_output=True).returncode == 0
    except OSError:
        return False


@unittest.skipUnless(rosetta(), 'needs Rosetta to run the x86_64 shim')
class ShimTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        out = subprocess.run([sys.executable, '-I', str(MAKE), cls.tmp.name], capture_output=True, text=True)
        assert out.returncode == 0, out.stderr
        cls.artifact = pathlib.Path(out.stdout.strip())
        cls.dylib = cls.artifact / 'libmetalold-selectors.dylib'
        cls.fake = pathlib.Path(cls.tmp.name) / 'fake_stack'
        subprocess.run(['clang', '-arch', 'x86_64', '-fobjc-arc', '-framework', 'Foundation', '-o', str(cls.fake),
                        str(HERE / 'fake_stack.m')], check=True, capture_output=True)
        subprocess.run(['codesign', '-f', '-s', '-', str(cls.fake)], check=True, capture_output=True)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def run_fake(self, env=None):
        full = dict(os.environ, **(env or {}))
        return subprocess.run([str(self.fake), str(self.dylib)], capture_output=True, text=True, env=full)

    # The host's own x86_64 Metal is in the test process (Foundation loads it) under the real class names, and its
    # classes cannot be initialized under Rosetta here, so the constructor stays off (METALOLD_SELECTORS=0, which is
    # also the kill switch under test) and the stand-ins are attached through the prefixed entry point. The
    # constructor path runs for real in `make --survey` against a legacy runtime (test_survey_on_legacy_stack).
    def test_answers(self):
        p = self.run_fake({'METALOLD_SELECTORS': '0'})
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn('constructor attached: 0', p.stdout)
        self.assertIn('PASSED: 0 failures', p.stdout)
        self.assertNotIn('FAIL', p.stdout)
        self.assertGreater(p.stdout.count('\nok '), 30)

    @unittest.skipUnless(os.environ.get('METALOLD_SELECTORS_LEGACY_RUNTIME') and os.environ.get('METALOLD_SELECTORS_TARGET_PROTOCOLS'),
                         'set METALOLD_SELECTORS_LEGACY_RUNTIME (a cache/shaders replay runtime) and METALOLD_SELECTORS_TARGET_PROTOCOLS')
    def test_survey_on_legacy_stack(self):
        import json
        with tempfile.TemporaryDirectory() as tmp:
            out = subprocess.run([sys.executable, '-I', str(MAKE), tmp, '--survey', os.environ['METALOLD_SELECTORS_LEGACY_RUNTIME'],
                                  '--protocols', os.environ['METALOLD_SELECTORS_TARGET_PROTOCOLS']], capture_output=True, text=True)
            self.assertEqual(out.returncode, 0, out.stderr)
            artifact = pathlib.Path(out.stdout.strip())
            after = json.loads((artifact / 'survey-after.json').read_text())
        device = after['classes']['MTLIGAccelDevice']
        still = {m['selector'] for m in device['missing']}
        for sel in ('-supportsDynamicAttributeStride', '-newResidencySetWithDescriptor:error:', '-newIOFileHandleWithURL:error:',
                    '-sparseTileSizeWithTextureType:pixelFormat:sampleCount:sparsePageSize:', '-maximumConcurrentCompilationTaskCount'):
            self.assertNotIn(sel, still)

    def test_macho(self):
        info = subprocess.run(['otool', '-hv', '-L', str(self.dylib)], capture_output=True, text=True).stdout
        self.assertIn('X86_64', info)
        self.assertIn('DYLIB', info)
        self.assertIn('/usr/lib/libmetalold-selectors.dylib', info)
        self.assertNotIn('Metal.framework', info, 'the shim must not link Metal: it loads inside Metal')
        sign = subprocess.run(['codesign', '-dv', str(self.dylib)], capture_output=True, text=True).stderr
        self.assertIn('Signature=adhoc', sign)

    def test_build_record(self):
        import json
        record = json.loads((self.artifact / 'build.json').read_text())
        self.assertEqual(record['unit'], 'patches/metalold-selectors/build')
        self.assertIn('src/metalold_selectors.m', record['inputs'])
        self.assertIn('libmetalold-selectors.dylib', record['outputs'])
        self.assertTrue(self.artifact.name.startswith('metalold-selectors-'))


class SurveyToolsTest(unittest.TestCase):
    def test_parse_protocols(self):
        sample = '''@protocol MTLDevice <NSObject>
@required
-[MTLDevice name];
@optional
-[MTLDevice newResidencySetWithDescriptor:error:];
@end
@interface MTLBufferDescriptor : NSObject <NSCopying> {
}
-[MTLBufferDescriptor length];
@end
'''
        with tempfile.TemporaryDirectory() as tmp:
            src = pathlib.Path(tmp) / 'objc.txt'
            src.write_text(sample)
            out = pathlib.Path(tmp) / 'p.json'
            subprocess.run([sys.executable, '-I', str(UNIT / 'survey' / 'parse_protocols.py'), str(src), str(out)], check=True, capture_output=True)
            import json
            data = json.loads(out.read_text())
        self.assertEqual(data['protocols']['MTLDevice']['parents'], ['NSObject'])
        self.assertEqual(data['protocols']['MTLDevice']['methods'], [['-', 'name', True], ['-', 'newResidencySetWithDescriptor:error:', False]])
        self.assertEqual(data['classes']['MTLBufferDescriptor'], {'super': 'NSObject', 'methods': [['-', 'length']]})


if __name__ == '__main__':
    unittest.main()
