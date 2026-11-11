"""Host tests for the CoreWiFi compat library (src/corewifi) and its install in front of Tahoe's CoreWiFi.

Always: the library is CoreWiFi re-exporting CoreWiFiTahoe.dylib; install.py renames a stand-in for the Tahoe donor
to CoreWiFiTahoe.dylib (install name and file) and installs the library as CoreWiFi, and skips (and says so) when the
native donors supplied no Tahoe CoreWiFi; make reads the kMethods table; methods.py's CoreWiFi modes (every class,
run-time added methods, Tahoe's classes answerable, strict) on authored ipsw-style text. Under Rosetta, against an
authored stand-in for Tahoe's CoreWiFi: the two symbols resolve through the CoreWiFi install name, every table method
is added except the one the class already has (class_addMethod never replaces), and the stubs behave (zero getters,
ENOTSUP from the coexistence and colocated-scan calls, activateWithCompletion: runs -activate then the block).

With COREWIFI_DONORS (a tahoe_native_donors artifact with Tahoe's CoreWiFi) and COREWIFI_COMPAT (a corewifi_compat
dylib) on a macOS 27 host, both installed by install.py's own code into a temporary framework: the real Tahoe
CoreWiFi overrides the host's cached CoreWiFi while 27.x's cached CoreWLAN is loaded too, no class is implemented
twice, the CWF classes come from CoreWiFiTahoe.dylib, all 16 table methods are added, and a Swift type lookup in
CoreWiFi's module scans the image without faulting.
Run: python3.14 -I -B -m unittest discover -s patches/golden-gate-userspace/tests -p 'test_corewifi*'"""
import importlib.util
import os
from pathlib import Path
import platform
import runpy
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

HERE = Path(__file__).resolve().parent
SRC = HERE.parent/'src'
MAKE = runpy.run_path(str(SRC/'corewifi/make'), run_name='corewifi_make')
FRAMEWORK = '/System/Library/PrivateFrameworks/CoreWiFi.framework/Versions/A'
DONOR = 'System/Library/PrivateFrameworks/CoreWiFi.framework/Versions/A'


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


install = load('corewifi_install', SRC/'install.py')
methods = load('corewifi_methods', SRC/'io80211/methods.py')

STAND_IN = r'''
#import <Foundation/Foundation.h>
#define EXPORT __attribute__((visibility("default")))
EXPORT @interface CWFActivity : NSObject @end @implementation CWFActivity @end
EXPORT @interface CWFAutoJoinMetric : NSObject @end @implementation CWFAutoJoinMetric @end
EXPORT @interface CWFConfigurationProfileManager : NSObject @property BOOL activated; @end
@implementation CWFConfigurationProfileManager - (void)activate { self.activated = YES; } @end
EXPORT @interface CWFInterface : NSObject @end @implementation CWFInterface @end
EXPORT @interface CWFWiFiUserAgent : CWFInterface @end @implementation CWFWiFiUserAgent @end
EXPORT @interface CWFNetworkProfile : NSObject @end
@implementation CWFNetworkProfile - (BOOL)isConnectivityAssistEnabled { return YES; } @end
EXPORT @interface CWFScanParameters : NSObject @end @implementation CWFScanParameters @end
EXPORT @interface CWFScanResult : NSObject @end @implementation CWFScanResult @end
@interface CWFUIScanManager : NSObject @end @implementation CWFUIScanManager @end
'''

PROBE = r'''
#import <Foundation/Foundation.h>
#import <objc/message.h>
#import <objc/runtime.h>
#include <dlfcn.h>
#define SEND(type, obj, name) ((type (*)(id, SEL))objc_msgSend)(obj, sel_registerName(name))
int main(int argc, char **argv) { @autoreleasepool {
    void *h = dlopen("/System/Library/PrivateFrameworks/CoreWiFi.framework/Versions/A/CoreWiFi", RTLD_NOW);
    if (!h) { printf("dlopen %s\n", dlerror()); return 2; }
    if (argc > 1 && !dlopen(argv[1], RTLD_NOW)) { printf("dlopen %s\n", dlerror()); return 2; }
    void **slot = dlsym(h, "CWFEventLinkQualityAssessmentKey");
    NSString *key = slot ? (__bridge NSString *)*slot : nil;
    unsigned *added = dlsym(h, "CoreWiFiCompatMethodsAdded");
    Class consent = objc_getClass("CWFColocatedConsentResult");
    Dl_info info; dladdr((__bridge void *)objc_getClass("CWFInterface"), &info);
    printf("key=%s consent=%s added=%u interface_image=%s\n", key ? key.UTF8String : "-",
           consent ? class_getName(class_getSuperclass(consent)) : "-", added ? *added : 0,
           strrchr(info.dli_fname, '/') + 1);
    id activity = [[objc_getClass("CWFActivity") alloc] init];
    id metric = [[objc_getClass("CWFAutoJoinMetric") alloc] init];
    ((void (*)(id, SEL, unsigned long long))objc_msgSend)(metric, sel_registerName("setOutcome:"), 7);
    id profile = [[objc_getClass("CWFNetworkProfile") alloc] init];
    ((void (*)(id, SEL, BOOL))objc_msgSend)(profile, sel_registerName("setConnectivityAssistEnabled:"), YES);
    id scan = [[objc_getClass("CWFScanParameters") alloc] init];
    ((void (*)(id, SEL, unsigned long long))objc_msgSend)(scan, sel_registerName("setExcludeScanFlags:"), 3);
    id result = [[objc_getClass("CWFScanResult") alloc] init];
    printf("pid=%d outcome=%llu duration=%llu carplay=%lld assist=%d mrsno=%d rsn=%d dict=%d/%d\n",
           SEND(int, activity, "processIdentifier"), SEND(unsigned long long, metric, "outcome"),
           SEND(unsigned long long, metric, "duration"), SEND(long long, profile, "carPlayCheckForInternetState"),
           SEND(BOOL, profile, "isConnectivityAssistEnabled"), SEND(BOOL, result, "mrsnoConnection"),
           SEND(BOOL, result, "rsnPriority"), SEND(BOOL, @{}, "mrsnoConnection"), SEND(BOOL, @{}, "rsnPriority"));
    id agent = [[objc_getClass("CWFWiFiUserAgent") alloc] init];
    NSError *error = nil;
    BOOL ok = ((BOOL (*)(id, SEL, id, NSError **))objc_msgSend)(agent,
        sel_registerName("setBTCoexActiveProfileName:error:"), @"profile", &error);
    dispatch_semaphore_t done = dispatch_semaphore_create(0);
    __block long scanCode = -1;
    ((void (*)(id, SEL, id))objc_msgSend)(agent, sel_registerName("performColocatedNetworkScanWithConsentStatus:"),
        ^(id r, NSError *e) { (void)r; scanCode = e.code; dispatch_semaphore_signal(done); });
    long waited = dispatch_semaphore_wait(done, dispatch_time(DISPATCH_TIME_NOW, 10 * NSEC_PER_SEC));
    printf("coex=%d/%ld scan=%ld/%ld\n", ok, (long)error.code, scanCode, waited);
    if (argc == 1) {
        id manager = [[objc_getClass("CWFConfigurationProfileManager") alloc] init];
        __block BOOL seen = NO;
        ((void (*)(id, SEL, id))objc_msgSend)(manager, sel_registerName("activateWithCompletion:"), ^{
            seen = SEND(BOOL, manager, "activated"); dispatch_semaphore_signal(done); });
        waited = dispatch_semaphore_wait(done, dispatch_time(DISPATCH_TIME_NOW, 10 * NSEC_PER_SEC));
        SEND(void, [[objc_getClass("CWFUIScanManager") alloc] init], "resetCache");
        printf("activate=%d/%ld\n", seen, waited);
    } else {
        printf("responds=%d\n", (int)[objc_getClass("CWFUIScanManager") instancesRespondToSelector:@selector(resetCache)]
               + (int)[objc_getClass("CWFConfigurationProfileManager")
                       instancesRespondToSelector:sel_registerName("activateWithCompletion:")]);
    }
    return 0;
}}
'''

SWIFT = r'''
import Darwin
guard dlopen("/System/Library/PrivateFrameworks/CoreWiFi.framework/Versions/A/CoreWiFi", RTLD_NOW) != nil else {
    print("dlopen failed: \(String(cString: dlerror()))"); exit(2) }
let type = _typeByName("8CoreWiFi12WIMACAddressV")
print("swift type=\(type != nil) hashable=\((type as? any Hashable.Type) != nil) int=\((Int.self as Any.Type as? any Hashable.Type) != nil)")
'''


def sh(*args, **kw):
    return subprocess.run(list(map(str, args)), check=True, capture_output=True, text=True, **kw).stdout


def install_into(system, donor, compat):
    """install.install_corewifi into SYSTEM (a temporary root) with DONOR at CoreWiFi's path, as the image has it
    after the native donors; ownership changes are skipped (not root)."""
    folder = Path(system)/DONOR
    folder.mkdir(parents=True, exist_ok=True)
    shutil.copy(donor, folder/'CoreWiFi')
    workspace = Path(system)/'workspace'
    workspace.mkdir(exist_ok=True)
    with mock.patch.object(install.os, 'chown'):
        return install.install_corewifi(Path(system), Path(compat), workspace), folder


def compile_probe(tmp, source=PROBE, name='probe'):
    (tmp/f'{name}.m').write_text(source)
    sh('xcrun', 'clang', '-arch', 'x86_64', '-fobjc-arc', '-framework', 'Foundation', tmp/f'{name}.m', '-o', tmp/name)
    sh('codesign', '-f', '-s', '-', tmp/name)
    return tmp/name


def run_probe(probe, folder, *args):
    # DYLD_FRAMEWORK_PATH finds the framework binary (CoreWiFi); CoreWiFiTahoe.dylib, not a framework binary name,
    # through DYLD_LIBRARY_PATH. On the image both are files at the real path and dyld needs no variable.
    frameworks = Path(folder).parents[2]
    env = dict(os.environ, DYLD_FRAMEWORK_PATH=str(frameworks), DYLD_LIBRARY_PATH=str(folder))
    return subprocess.run([str(probe), *map(str, args)], capture_output=True, text=True, env=env, timeout=120)


def stand_in(tmp):
    (tmp/'stand_in.m').write_text(STAND_IN)
    out = tmp/'TahoeStandIn'
    sh('xcrun', 'clang', '-arch', 'x86_64', '-fobjc-arc', '-dynamiclib', '-fvisibility=hidden',
       '-install_name', f'{FRAMEWORK}/CoreWiFi', '-Wl,-headerpad,0x100', '-framework', 'Foundation',
       tmp/'stand_in.m', '-o', out)
    sh('codesign', '-f', '-s', '-', out)
    return out


class Library(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix='corewifi-compat-test-'))
        cls.lib = MAKE['build'](cls.tmp)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp)

    def test_install_name_and_reexport(self):
        self.assertEqual(sh('otool', '-D', self.lib).splitlines()[-1], f'{FRAMEWORK}/CoreWiFi')
        libs = sh('otool', '-L', self.lib)
        self.assertIn(f'{FRAMEWORK}/CoreWiFiTahoe.dylib (compatibility version', libs)
        self.assertIn('reexport', libs)
        exports = {line.split()[-1] for line in sh('dyld_info', '-exports', self.lib).splitlines()
                   if line.strip().startswith('0x') and 're-export' not in line}
        self.assertTrue({'_CWFEventLinkQualityAssessmentKey', '_OBJC_CLASS_$_CWFColocatedConsentResult'} <= exports)

    def test_method_table(self):
        table = MAKE['added_methods']()
        self.assertEqual(sum(map(len, table.values())), 16)
        self.assertIn('-activateWithCompletion:', table['CWFConfigurationProfileManager'])
        self.assertEqual(table['NSDictionary'], {'-mrsnoConnection', '-rsnPriority'})

    def test_install_renames_the_donor_and_puts_the_library_in_front(self):
        system = self.tmp/'system'
        result, folder = install_into(system, stand_in(self.tmp), self.lib)
        self.assertIn('CoreWiFiTahoe.dylib', result)
        self.assertEqual(sh('otool', '-D', folder/'CoreWiFiTahoe.dylib').splitlines()[-1],
                         f'{FRAMEWORK}/CoreWiFiTahoe.dylib')
        self.assertEqual((folder/'CoreWiFi').read_bytes(), self.lib.read_bytes())
        sh('codesign', '--verify', folder/'CoreWiFiTahoe.dylib')

    def test_without_a_tahoe_donor_nothing_is_installed(self):
        system = self.tmp/'bare'
        (system/DONOR).mkdir(parents=True)
        with mock.patch('builtins.print'):
            result = install.install_corewifi(system, self.lib, self.tmp)
        self.assertTrue(result.startswith('not installed'))
        self.assertEqual(list((system/DONOR).iterdir()), [])

    @unittest.skipUnless(platform.machine() == 'arm64', 'runs the x86_64 libraries under Rosetta')
    def test_compat_against_stand_in(self):
        tmp = self.tmp/'rosetta'
        tmp.mkdir()
        _, folder = install_into(tmp/'system', stand_in(tmp), self.lib)
        out = run_probe(compile_probe(tmp), folder)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr[-2000:])
        lines = out.stdout.splitlines()
        self.assertEqual(lines[0], 'key=LinkQualityAssessment consent=NSObject added=15 '
                                   'interface_image=CoreWiFiTahoe.dylib')
        # isConnectivityAssistEnabled keeps the stand-in's own YES: the table never replaces a method
        self.assertEqual(lines[1], 'pid=0 outcome=0 duration=0 carplay=0 assist=1 mrsno=0 rsn=0 dict=0/0')
        self.assertEqual(lines[2], 'coex=0/45 scan=45/0')
        self.assertEqual(lines[3], 'activate=1/0')


class Methods(unittest.TestCase):
    """methods.py's CoreWiFi modes."""
    BUILD = methods.parse_classes('''
@interface CWFInterface : NSObject
-[CWFInterface activate];
-[CWFInterface setBTCoexActiveProfileName:error:];
@end
@interface CWFWiFiUserAgent : CWFInterface
-[CWFWiFiUserAgent start];
@end
@interface CWFLinkQualityAssessment : NSObject
-[CWFLinkQualityAssessment qualityAssessment];
@end
''')
    TAHOE = methods.parse_classes('''
@interface CWFInterface : NSObject
-[CWFInterface activate];
@end
@interface CWFWiFiUserAgent : CWFInterface
-[CWFWiFiUserAgent start];
@end
''')

    def providers(self, compat=None):
        return [('compat', set(), compat or {}), ('Tahoe CoreWiFi', {'CWFInterface'}, self.TAHOE)]

    def test_unbound_classes_and_inherited_gaps(self):
        clients = {'usr/sbin/WirelessRadioManagerd [arm64e]': (set(), {'setBTCoexActiveProfileName:error:',
                                                                        'qualityAssessment', 'start'})}
        report = methods.assess(self.BUILD, self.providers(), set(), clients, every_class=True)
        self.assertEqual(report['CWFWiFiUserAgent']['provider'], 'Tahoe CoreWiFi')  # defined, not exported
        errors, reported, _ = methods.classify(report, 'CoreWiFi', ('compat', 'Tahoe CoreWiFi'), strict=True)
        self.assertEqual(len(errors), 2)
        self.assertTrue(all('setBTCoexActiveProfileName:error:' in e for e in errors))
        self.assertEqual(len(reported), 1)
        self.assertIn('[CWFLinkQualityAssessment qualityAssessment] from nothing', reported[0])
        # bound classes only (the IO80211/WiFiPeerToPeer mode): nothing is checked for this client
        self.assertEqual(methods.assess(self.BUILD, self.providers(), set(), clients), {})

    def test_added_method_on_a_superclass_answers_subclasses(self):
        compat = {'CWFInterface': {'super': None, 'defined': False, 'methods': {'-setBTCoexActiveProfileName:error:'}}}
        clients = {methods.CACHE: (set(), {'setBTCoexActiveProfileName:error:'})}
        report = methods.assess(self.BUILD, self.providers(compat), set(), clients, every_class=True)
        self.assertEqual(methods.classify(report, 'CoreWiFi', ('compat', 'Tahoe CoreWiFi'), strict=True), ([], [], []))

    def test_strict_fails_on_cache_only_gaps(self):
        clients = {methods.CACHE: (set(), {'setBTCoexActiveProfileName:error:'})}
        report = methods.assess(self.BUILD, self.providers(), set(), clients, every_class=True)
        self.assertEqual(len(methods.classify(report, 'CoreWiFi', ('compat', 'Tahoe CoreWiFi'))[1]), 2)
        self.assertEqual(len(methods.classify(report, 'CoreWiFi', ('compat', 'Tahoe CoreWiFi'), strict=True)[0]), 2)


DONORS, COMPAT = os.environ.get('COREWIFI_DONORS'), os.environ.get('COREWIFI_COMPAT')


def macos_major():
    return int(platform.mac_ver()[0].split('.')[0] or 0)


@unittest.skipUnless(DONORS and COMPAT, 'set COREWIFI_DONORS and COREWIFI_COMPAT')
@unittest.skipUnless(platform.machine() == 'arm64' and macos_major() >= 27, 'needs Rosetta on a macOS 27 host')
class Artifact(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix='corewifi-artifact-test-'))
        _, cls.folder = install_into(cls.tmp/'system', Path(DONORS)/'payload/system'/DONOR/'CoreWiFi', COMPAT)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp)

    def test_override_with_cached_clients(self):
        out = run_probe(compile_probe(self.tmp), self.folder,
                        '/System/Library/Frameworks/CoreWLAN.framework/Versions/A/CoreWLAN')
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr[-3000:])
        self.assertNotIn('is implemented in both', out.stderr)
        lines = out.stdout.splitlines()
        self.assertEqual(lines[0], 'key=LinkQualityAssessment consent=NSObject added=16 '
                                   'interface_image=CoreWiFiTahoe.dylib')
        self.assertEqual(lines[1], 'pid=0 outcome=0 duration=0 carplay=0 assist=0 mrsno=0 rsn=0 dict=0/0')
        self.assertEqual(lines[2], 'coex=0/45 scan=45/0')
        self.assertEqual(lines[3], 'responds=2')

    def test_swift_type_lookup(self):
        (self.tmp/'probe.swift').write_text(SWIFT)
        sh('xcrun', 'swiftc', '-target', 'x86_64-apple-macos14', self.tmp/'probe.swift', '-o', self.tmp/'swiftprobe')
        sh('codesign', '-f', '-s', '-', self.tmp/'swiftprobe')
        out = run_probe(self.tmp/'swiftprobe', self.folder)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr[-3000:])
        self.assertEqual(out.stdout.strip(), 'swift type=true hashable=true int=true')


if __name__ == '__main__':
    unittest.main()
