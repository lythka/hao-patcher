"""Builds libhelloblur.dylib with make.sh and checks the Mach-O; with HELLOBLUR_REFERENCE (the row's file) also
checks compare.py accepts the rebuild. Skips without clang or the SDK. Nothing is run: the dylib is x86_64."""
import os
import pathlib
import runpy
import shutil
import subprocess
import tempfile
import unittest

BUILD = pathlib.Path(__file__).resolve().parents[1]
SDK = os.environ.get('HELLOBLUR_SDK', '/Library/Developer/CommandLineTools/SDKs/MacOSX26.sdk')
compare = runpy.run_path(str(BUILD / 'compare.py'))


@unittest.skipUnless(shutil.which('clang') and os.path.isdir(SDK), 'needs clang and %s' % SDK)
class Build(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        subprocess.run([str(BUILD / 'make.sh'), cls.tmp.name], check=True, capture_output=True)
        cls.dylib = os.path.join(cls.tmp.name, 'libhelloblur.dylib')

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def otool(self, *flags):
        return subprocess.run(['otool', *flags, self.dylib], check=True, capture_output=True, text=True).stdout

    def test_macho(self):
        self.assertIn('x86_64', subprocess.run(['lipo', '-archs', self.dylib], capture_output=True,
                                               text=True).stdout)
        loads = self.otool('-l')
        self.assertIn('name /usr/lib/libhelloblur.dylib', loads)
        self.assertIn('minos 13.0', loads)
        linked = self.otool('-L')
        self.assertNotIn('Metal.framework', linked)        # it is loaded by the Metal wrapper
        self.assertNotIn('AppKit.framework', linked)

    def test_signature(self):
        info = subprocess.run(['codesign', '-dv', self.dylib], capture_output=True, text=True).stderr
        self.assertIn('Identifier=local.libhelloblur', info)
        self.assertIn('flags=0x2(adhoc)', info)

    def test_self_compare(self):
        self.assertEqual(compare['compare'](self.dylib, self.dylib), (True, 'identical'))

    @unittest.skipUnless(os.environ.get('HELLOBLUR_REFERENCE'), 'HELLOBLUR_REFERENCE not set')
    def test_reference(self):
        ok, text = compare['compare'](os.environ['HELLOBLUR_REFERENCE'], self.dylib)
        self.assertTrue(ok, text)


class Stamps(unittest.TestCase):
    def test_not_macho(self):
        with self.assertRaises(SystemExit):
            compare['stamps'](b'\0' * 64)


if __name__ == '__main__':
    unittest.main()
