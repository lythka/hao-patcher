"""Host tests for the WiFiPeerToPeer compat library (src/wifip2p): it exports exactly the 36 Wi-Fi Aware classes and
their metaclasses, links only libobjc and libSystem and re-exports WiFiPeerToPeerOCLP.dylib; under Rosetta a category on
one of its classes loads (the APTransport case that aborted coreaudiod), and the stand-ins answer the methods 27.x's
WiFiAgent and airportd send them. The coverage check names the library it checks; the method check (io80211/methods.py)
parses ipsw's ObjC text and fails only on stand-in gaps an on-disk client names. With WIFIP2P_OCLP_DIR set to OCLP's WiFiPeerToPeer folder (PatcherSupportPkg 13.7.2-25, holding WiFiPeerToPeer
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
SOURCE = (SRC/'wifip2p/wifip2p_compat.m').read_text()
CLASSES = re.findall(r'^WIFI_AWARE_CLASS\((\w+)\)$', SOURCE, re.M) + re.findall(r'^STAND_IN @interface (\w+) ', SOURCE, re.M)
spec = importlib.util.spec_from_file_location('wifip2p_methods', SRC/'io80211/methods.py')
methods = importlib.util.module_from_spec(spec)
spec.loader.exec_module(methods)


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

    @unittest.skipUnless(platform.machine() == 'arm64', 'runs the x86_64 library under Rosetta')
    def test_stand_ins_answer_27_methods(self):
        folder = self.tmp/'methods'
        folder.mkdir()
        (self.tmp/'empty2.c').write_text('int wifip2p_compat_test_stand_in;\n')
        sh('xcrun', 'clang', '-arch', 'x86_64', '-dynamiclib', '-install_name', f'{FRAMEWORK}/WiFiPeerToPeerOCLP.dylib',
           self.tmp/'empty2.c', '-o', folder/'WiFiPeerToPeerOCLP.dylib')
        sh('codesign', '-f', '-s', '-', folder/'WiFiPeerToPeerOCLP.dylib')
        shutil.copy(self.lib, folder/'WiFiPeerToPeer')
        # What 27.x's WiFiAgent does in -[CWWiFiAgent init] outside the system context, then at teardown.
        (self.tmp/'agent.m').write_text(
            '#import <objc/NSObject.h>\n#import <stdio.h>\n'
            '@interface WiFiP2PKeychainAgent : NSObject - (void)activate; - (void)invalidate; @end\n'
            '@interface WiFiAwareStateMonitor : NSObject - (void)beginMonitoring; - (void)endMonitoring;'
            ' - (void)setStateUpdatedHandler:(id)h; - (void)setRealtimeModeUpdatedHandler:(id)h; @end\n'
            'int main(void) { WiFiP2PKeychainAgent *a = [WiFiP2PKeychainAgent new]; [a activate]; [a invalidate];'
            ' WiFiAwareStateMonitor *m = [WiFiAwareStateMonitor new]; [m setStateUpdatedHandler:^{}];'
            ' [m setRealtimeModeUpdatedHandler:^{}]; [m beginMonitoring]; [m endMonitoring];'
            ' printf("%d\\n", (int)[a respondsToSelector:@selector(activate)]); return 0; }\n')
        probe = self.tmp/'agent'
        sh('xcrun', 'clang', '-arch', 'x86_64', '-fobjc-arc', self.tmp/'agent.m', folder/'WiFiPeerToPeer', '-lobjc',
           '-o', probe)
        sh('codesign', '-f', '-s', '-', probe)
        self.assertEqual(sh(probe, env={**os.environ, 'DYLD_LIBRARY_PATH': str(folder)}).strip(), '1')

    @unittest.skipUnless(shutil.which('ipsw'), 'needs ipsw')
    def test_method_check_reads_the_stand_ins(self):
        classes = methods.parse_classes(sh('ipsw', 'macho', 'info', self.lib, '--objc', '--no-color'))
        self.assertEqual({name for name in classes if classes[name]['defined']}, set(CLASSES))
        self.assertEqual(classes['WiFiP2PKeychainAgent']['methods'], {'-activate', '-invalidate'})
        self.assertEqual(classes['WiFiAwareStateMonitor']['methods'],
                         {'-beginMonitoring', '-endMonitoring', '-setStateUpdatedHandler:',
                          '-setRealtimeModeUpdatedHandler:'})

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


class Methods(unittest.TestCase):
    """io80211/methods.py on ipsw-style text."""
    BUILD = methods.parse_classes('''
@interface WiFiP2PKeychainAgent : NSObject {
}
/* instance methods */
-[WiFiP2PKeychainAgent init];
-[WiFiP2PKeychainAgent activate];
-[WiFiP2PKeychainAgent invalidate];
-[WiFiP2PKeychainAgent exportedObject];
@end
@interface WiFiP2PUIAgent : NSObject <NSObject> {
}
-[WiFiP2PUIAgent activate];
@end
@interface WiFiChannel : NSObject
+[WiFiChannel supportsSecureCoding];
-[WiFiChannel is6GHz];
-[WiFiChannel channelNumber];
@end
''')
    WRAPPER = methods.parse_classes('''
@interface WiFiChannel (Stub)

/* instance methods */
-[WiFiChannel  is6GHz];

@end
''')

    def providers(self, compat_methods=()):
        compat = {'WiFiP2PKeychainAgent': {'super': 'NSObject', 'defined': True, 'methods': set(compat_methods)}}
        return [('compat', {'WiFiP2PKeychainAgent'}, compat), ('OCLP WiFiPeerToPeer', set(), self.WRAPPER),
                ('OCLP WiFiPeerToPeerOld.dylib', {'WiFiP2PUIAgent', 'WiFiChannel'}, {})]

    def test_parse(self):
        self.assertTrue(self.BUILD['WiFiP2PKeychainAgent']['defined'])
        self.assertEqual(self.BUILD['WiFiP2PKeychainAgent']['super'], 'NSObject')
        self.assertEqual(self.BUILD['WiFiChannel']['methods'], {'+supportsSecureCoding', '-is6GHz', '-channelNumber'})
        self.assertFalse(self.WRAPPER['WiFiChannel']['defined'])
        self.assertEqual(self.WRAPPER['WiFiChannel']['methods'], {'-is6GHz'})
        self.assertEqual(methods.parse_selrefs('@class refs\n0x1 => 0x2: Foo\n@selectors refs\n'
                                               '0x10 => 0x20: activate\n0x11 => 0x21: initWithFoo:bar:\n'),
                         {'activate', 'initWithFoo:bar:'})

    def test_stand_in_gap_named_on_disk_fails(self):
        clients = {'System/Library/CoreServices/WiFiAgent.app/Contents/MacOS/WiFiAgent [arm64e]':
                   ({'WiFiP2PKeychainAgent'}, {'new', 'init', 'activate', 'invalidate'})}
        report = methods.assess(self.BUILD, self.providers(), {'-init'}, clients)
        self.assertEqual(sorted(report['WiFiP2PKeychainAgent']['gaps']), ['-activate', '-invalidate'])
        errors, reported, _ = methods.classify(report, 'WiFiPeerToPeer')
        self.assertEqual(len(errors), 2)
        self.assertIn('-[WiFiP2PKeychainAgent activate] from compat', errors[0])
        self.assertEqual(reported, [])
        report = methods.assess(self.BUILD, self.providers(('-activate', '-invalidate')), {'-init'}, clients)
        self.assertEqual(methods.classify(report, 'WiFiPeerToPeer'), ([], [], []))

    def test_cache_only_gap_and_unreadable_oclp_are_reported(self):
        clients = {methods.CACHE: ({'WiFiP2PKeychainAgent', 'WiFiChannel', 'WiFiP2PUIAgent'},
                                   {'activate', 'is6GHz', 'channelNumber'})}
        errors, reported, unverified = methods.classify(
            methods.assess(self.BUILD, self.providers(), set(), clients), 'WiFiPeerToPeer')
        self.assertEqual(errors, [])
        self.assertEqual(reported, ['-[WiFiP2PKeychainAgent activate] from compat (named by shared cache)'])
        # Old's own WiFiChannel list is unreadable; the wrapper's (Stub) category answers is6GHz.
        self.assertEqual(unverified, [
            'OCLP WiFiPeerToPeerOld.dylib WiFiChannel: own methods unreadable offline; clients name -channelNumber',
            'OCLP WiFiPeerToPeerOld.dylib WiFiP2PUIAgent: own methods unreadable offline; clients name -activate'])


if __name__ == '__main__':
    unittest.main()
