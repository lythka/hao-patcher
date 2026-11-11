"""Host tests for the WiFiPeerToPeer compat library (src/wifip2p): it exports exactly the 36 Wi-Fi Aware classes and
their metaclasses, links only libobjc and libSystem and re-exports WiFiPeerToPeerOCLP.dylib; under Rosetta a category on
one of its classes loads (the APTransport case that aborted coreaudiod). The coverage check names the library it
checks. With WIFIP2P_OCLP_DIR set to OCLP's WiFiPeerToPeer folder (PatcherSupportPkg 13.7.2-25, holding WiFiPeerToPeer
and WiFiPeerToPeerOld.dylib), the wrapper can be renamed in place and keeps its exports, and the compat library
exports no name the wrapper already has.
Run: python3.14 -I -m unittest discover -s patches/golden-gate-userspace/tests -p 'test_wifip2p*'"""
import importlib.util
import os
from pathlib import Path
import platform
import re
import runpy
import shutil
import subprocess
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
SRC = HERE.parent/'src'
MAKE = runpy.run_path(str(SRC/'wifip2p/make'))
spec = importlib.util.spec_from_file_location('macho_wifip2p', SRC/'macho.py')
macho = importlib.util.module_from_spec(spec)
spec.loader.exec_module(macho)
spec = importlib.util.spec_from_file_location('wifip2p_coverage', SRC/'io80211/coverage.py')
coverage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(coverage)
FRAMEWORK = '/System/Library/PrivateFrameworks/WiFiPeerToPeer.framework/Versions/A'
CLASSES = re.findall(r'^WIFI_AWARE_CLASS\((\w+)\)$', (SRC/'wifip2p/wifip2p_compat.m').read_text(), re.M)


def sh(*args, **kw):
    return subprocess.run(list(map(str, args)), check=True, capture_output=True, text=True, **kw).stdout


def exports(path):
    return sorted(line.split()[-1] for line in sh('dyld_info', '-exports', path).splitlines()
                  if line.strip().startswith('0x') and 're-export' not in line)


class Library(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix='wifip2p-compat-test-'))
        cls.lib = MAKE['build'](cls.tmp)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp)

    def test_class_list(self):
        self.assertEqual(len(CLASSES), 36)
        self.assertEqual(len(set(CLASSES)), 36)
        self.assertIn('WiFiAwareDatapathInfo', CLASSES)

    def test_exports_and_load_commands(self):
        self.assertEqual(exports(self.lib), sorted(f'_OBJC_{kind}_$_{name}' for name in CLASSES
                                                   for kind in ('CLASS', 'METACLASS')))
        libs = sh('otool', '-L', self.lib).splitlines()[1:]
        self.assertEqual(sorted(line.split()[0] for line in libs),
                         [f'{FRAMEWORK}/WiFiPeerToPeer', f'{FRAMEWORK}/WiFiPeerToPeerOCLP.dylib',
                          '/usr/lib/libSystem.B.dylib', '/usr/lib/libobjc.A.dylib'])
        self.assertTrue(any('WiFiPeerToPeerOCLP.dylib' in line and 'reexport' in line for line in libs))
        self.assertEqual(sh('otool', '-D', self.lib).splitlines()[-1], f'{FRAMEWORK}/WiFiPeerToPeer')

    @unittest.skipUnless(platform.machine() == 'arm64', 'runs the x86_64 library under Rosetta')
    def test_a_category_on_a_compat_class_loads(self):
        folder = self.tmp/'reexport'
        folder.mkdir()
        (self.tmp/'empty.c').write_text('int wifip2p_compat_test_stand_in;\n')
        sh('xcrun', 'clang', '-arch', 'x86_64', '-dynamiclib', '-install_name', f'{FRAMEWORK}/WiFiPeerToPeerOCLP.dylib',
           self.tmp/'empty.c', '-o', folder/'WiFiPeerToPeerOCLP.dylib')
        sh('codesign', '-f', '-s', '-', folder/'WiFiPeerToPeerOCLP.dylib')
        shutil.copy(self.lib, folder/'WiFiPeerToPeer')
        (self.tmp/'probe.m').write_text(
            '#import <objc/NSObject.h>\n#import <stdio.h>\n'
            '@interface WiFiAwareDatapathInfo : NSObject @end\n'
            '@interface WiFiAwareDatapathInfo (Probe) - (int)probe; @end\n'
            '@implementation WiFiAwareDatapathInfo (Probe) - (int)probe { return 42; } @end\n'
            'int main(void) { WiFiAwareDatapathInfo *i = [WiFiAwareDatapathInfo new];'
            ' printf("%d\\n", [i probe]); return 0; }\n')
        probe = self.tmp/'probe'
        sh('xcrun', 'clang', '-arch', 'x86_64', '-fobjc-arc', self.tmp/'probe.m', folder/'WiFiPeerToPeer', '-lobjc',
           '-o', probe)
        sh('codesign', '-f', '-s', '-', probe)
        out = sh(probe, env={**os.environ, 'DYLD_LIBRARY_PATH': str(folder)})
        self.assertEqual(out.strip(), '42')

    @unittest.skipUnless(os.environ.get('WIFIP2P_OCLP_DIR'),
                         "set WIFIP2P_OCLP_DIR to a folder with OCLP's WiFiPeerToPeer and WiFiPeerToPeerOld.dylib")
    def test_wrapper_renames_and_nothing_is_shadowed(self):
        oclp = Path(os.environ['WIFIP2P_OCLP_DIR'])
        data = bytearray((oclp/'WiFiPeerToPeer').read_bytes())
        macho.set_install_name(data, f'{FRAMEWORK}/WiFiPeerToPeer'.encode(),
                               f'{FRAMEWORK}/WiFiPeerToPeerOCLP.dylib'.encode())
        renamed = self.tmp/'WiFiPeerToPeerOCLP.dylib'
        renamed.write_bytes(data)
        sh('codesign', '-f', '-s', '-', renamed)
        self.assertEqual(sh('otool', '-D', renamed).splitlines()[-1], f'{FRAMEWORK}/WiFiPeerToPeerOCLP.dylib')
        self.assertEqual(sh('dyld_info', '-exports', oclp/'WiFiPeerToPeer').splitlines()[1:],
                         sh('dyld_info', '-exports', renamed).splitlines()[1:])
        wrapped = set(exports(oclp/'WiFiPeerToPeer')) | set(exports(oclp/'WiFiPeerToPeerOld.dylib'))
        self.assertEqual(set(exports(self.lib)) & wrapped, set())


class Coverage(unittest.TestCase):
    def test_errors_name_the_library(self):
        _, errors = coverage.assess({'_OBJC_CLASS_$_Gone': {'shared cache'}}, {}, [('compat', {})], 'WiFiPeerToPeer')
        self.assertEqual(len(errors), 1)
        self.assertIn("nor OCLP's WiFiPeerToPeer", errors[0])

    def test_install_name(self):
        self.assertEqual(coverage.install_name('WiFiPeerToPeer'), f'{FRAMEWORK}/WiFiPeerToPeer\0'.encode())


if __name__ == '__main__':
    unittest.main()
