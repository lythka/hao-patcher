"""The Live Text runtime: detector rewrite, MPSNDArray redirect, coherence lifetimes, a real runtime build, and
Vision text recognition with the runtime loaded.

The recognition test runs the authored x86_64 fixture ocr_probe through Rosetta on an Apple silicon host. There
x86_64 Espresso has no backend for TextRecognition's default compute units ("No available backends on platform
[intel] for compute units ane"), as on the HD 4000; with the runtime loaded its policy selects the GPU and the
rewritten detector, and both authored lines must come back exactly. It uses the host's own TextRecognition
detector (no Apple file is stored in git) and is skipped when that or Rosetta is missing.
"""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from common import FIXTURES, UNIT, adapters, clang, run, sha, x86_runs
import detector
import runtime

HOST_DETECTOR = Path('/System/Library/PrivateFrameworks/TextRecognition.framework/Versions/A/Resources/'
                     'cr_td_model_v3_e5.mlmodelc.bundle')
LINES = ['Live Text on Intel', 'HD Graphics 4000']


class DetectorTests(unittest.TestCase):
    def test_rewrites_only_the_input_binding(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root/'original/weights').mkdir(parents=True)
            mil = 'head\n' + detector.PIXEL_INPUT + '\n' + detector.PIXEL_TO_TENSOR + '\ntail\n'
            (root/'original/model.mil').write_text(mil)
            (root/'original/weights/weight.bin').write_bytes(b'authored weights')
            record = detector.prepare(root/'original', root/'detector')
            text = (root/'detector/model.mil').read_text()
            self.assertEqual((root/'original/model.mil').read_text(), mil)
            self.assertIn(detector.TENSOR_INPUT, text)
            self.assertIn('transpose(x=diagnostic_bgra_hwc', text)
            self.assertNotIn('pixel_buffer', text)
            self.assertTrue(text.startswith('head\n') and text.endswith('\ntail\n'))
            self.assertEqual((root/'detector/weights/weight.bin').read_bytes(), b'authored weights')
            self.assertEqual(record['files']['weights/weight.bin'], sha(root/'original/weights/weight.bin'))
            with self.assertRaises(FileExistsError):
                detector.prepare(root/'original', root/'detector')
            (root/'other').mkdir()
            (root/'other/model.mil').write_text(mil.replace(detector.PIXEL_INPUT, 'changed'))
            with self.assertRaisesRegex(ValueError, 'MISSING/unsupported'):
                detector.prepare(root/'other', root/'other-out')


@unittest.skipUnless(x86_runs(), 'needs an x86_64-capable host (Rosetta on Apple silicon)')
class HelperTests(unittest.TestCase):
    def test_mpsndarray_redirect_only_for_the_hd4000(self):
        with tempfile.TemporaryDirectory() as tmp:
            test = clang(Path(tmp)/'library-test', '-fobjc-arc', '-fblocks', '-DLIVE_TEXT_LIBRARY_TEST',
                         UNIT/'src/library.m', FIXTURES/'library_test.m', '-framework', 'Foundation')
            self.assertIn('lazy resource controls passed', run(test))

    def test_coherence_lifetimes(self):
        with tempfile.TemporaryDirectory() as tmp:
            test = Path(tmp)/'state-test'
            run('xcrun', 'clang++', '-std=c++17', '-arch', 'x86_64', '-O1', '-I', UNIT/'src',
                FIXTURES/'test_state.cpp', '-o', test)
            run('codesign', '-f', '-s', '-', test)
            self.assertIn('10000 cleanup cycles', run(test))


@unittest.skipUnless(x86_runs(), 'needs an x86_64-capable host (Rosetta on Apple silicon)')
class RuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='live-text-runtime-')
        cls.root = Path(cls.tmp.name).resolve()
        cls.adapters = adapters(cls.root/'adapters')

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def build(self, name, detector_bundle):
        # Any file stands in for the repaired MPSNDArray library: only an HD 4000 device ever loads it.
        stand_in = self.root/(name + '.metallib')
        stand_in.write_bytes(b'authored metallib stand-in')
        folder = self.root/name
        return folder, runtime.build(folder, str(folder), self.adapters, stand_in, detector_bundle, stand_in)

    def test_build_records_every_file_and_install_name(self):
        source = self.root/'authored-detector'
        source.mkdir()
        (source/'model.mil').write_text(detector.PIXEL_INPUT + '\n' + detector.PIXEL_TO_TENSOR + '\n')
        folder, record = self.build('built', source)
        self.assertEqual(runtime.verify(folder), record)
        self.assertEqual(sorted(p for p in record['files'] if p.endswith('.dylib')),
                         sorted(runtime.HELPERS + [runtime.UMBRELLA]))
        for name in runtime.HELPERS + [runtime.UMBRELLA]:
            run('codesign', '--verify', '--strict', folder/name)
            self.assertEqual(run('otool', '-D', folder/name).splitlines()[-1], str(folder/name))
        needed = run('otool', '-L', folder/runtime.UMBRELLA)
        for name in runtime.HELPERS:
            self.assertIn(str(folder/name), needed)
        self.assertEqual(record['inputs']['MPSNDArray.metallib']['source'], runtime.SOURCE_METALLIB)

    @unittest.skipUnless(HOST_DETECTOR.is_dir(), 'host has no TextRecognition detector')
    def test_recognizes_text_with_the_runtime(self):
        folder, _ = self.build('host', HOST_DETECTOR)
        probe = clang(self.root/'ocr_probe', '-fobjc-arc', FIXTURES/'ocr_probe.m', '-framework', 'Foundation',
                      '-framework', 'CoreGraphics', '-framework', 'CoreText', '-framework', 'Vision')
        environment = dict(os.environ, HAO_LIVE_TEXT_DIAGNOSTICS='1',
                           DYLD_INSERT_LIBRARIES=str(folder/runtime.UMBRELLA))
        result = subprocess.run([str(probe), *LINES], env=environment, capture_output=True, text=True, timeout=900)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('hao: Live Text client support loaded', result.stderr)
        self.assertIn('event=detector-bind-pass', result.stderr)
        self.assertNotIn('detector-bind-refusal count', result.stderr)
        self.assertEqual(result.stdout.splitlines(), LINES)


if __name__ == '__main__':
    unittest.main()
