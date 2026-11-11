"""Host tests for the CoreWiFi compat library (corewifi_compat.m), under Rosetta, installed the way the image has it:
a CoreWiFi.framework/Versions/A holding CoreWiFi (the compat library) and CoreWiFiTahoe.dylib, found through
DYLD_FRAMEWORK_PATH/DYLD_LIBRARY_PATH so it overrides the host's cached CoreWiFi as the on-disk files override the
image's cache.

Always: against an authored stand-in for Tahoe's CoreWiFi (the classes the compat table names, one of which already
has a method the table lists), the two symbols resolve through the CoreWiFi install name, every table method is
added except the one the class already has (class_addMethod never replaces), and the stubs behave: zero getters,
ENOTSUP from the coexistence and colocated-scan calls, activateWithCompletion: runs -activate then the block.

With COREWIFI_ARTIFACT set to a corewifi-tahoe artifact folder (build/make's output) and a macOS 27 host: the real
CoreWiFiTahoe.dylib overrides the host's cached CoreWiFi while 27.x's cached CoreWLAN is loaded too; no class is
implemented twice, the CWF classes come from CoreWiFiTahoe.dylib, all 16 table methods are added, the two symbols
and the safe stubs answer, and a Swift conformance lookup scans the image without faulting.
Run: python3.14 -I -B -m unittest discover -s patches/golden-gate-corewifi/build/tests"""
import os
from pathlib import Path
import platform
import runpy
import shutil
import subprocess
import tempfile
import unittest

BUILD = Path(__file__).resolve().parents[1]
MAKE = runpy.run_path(str(BUILD/'make'), run_name='corewifi_make')
FRAMEWORK = '/System/Library/PrivateFrameworks/CoreWiFi.framework/Versions/A'

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


def compile_probe(tmp, source=PROBE, name='probe'):
    (tmp/f'{name}.m').write_text(source)
    sh('xcrun', 'clang', '-arch', 'x86_64', '-fobjc-arc', '-framework', 'Foundation', tmp/f'{name}.m', '-o', tmp/name)
    sh('codesign', '-f', '-s', '-', tmp/name)
    return tmp/name


def run_probe(probe, frameworks, *args):
    # DYLD_FRAMEWORK_PATH finds the framework binary (CoreWiFi); CoreWiFiTahoe.dylib, not a framework binary name,
    # through DYLD_LIBRARY_PATH. On the image both are files at the real path and dyld needs no variable.
    env = dict(os.environ, DYLD_FRAMEWORK_PATH=str(frameworks),
               DYLD_LIBRARY_PATH=str(Path(frameworks)/'CoreWiFi.framework/Versions/A'))
    return subprocess.run([str(probe), *map(str, args)], capture_output=True, text=True, env=env, timeout=120)


@unittest.skipUnless(platform.machine() == 'arm64', 'runs the x86_64 libraries under Rosetta')
class StandIn(unittest.TestCase):
    def test_compat_against_stand_in(self):
        with tempfile.TemporaryDirectory(prefix='corewifi-compat-test-') as tmp:
            tmp = Path(tmp)
            folder = tmp/'frameworks/CoreWiFi.framework/Versions/A'
            folder.mkdir(parents=True)
            (tmp/'stand_in.m').write_text(STAND_IN)
            tahoe = folder/'CoreWiFiTahoe.dylib'
            sh('xcrun', 'clang', '-arch', 'x86_64', '-fobjc-arc', '-dynamiclib', '-fvisibility=hidden',
               '-install_name', f'{FRAMEWORK}/CoreWiFiTahoe.dylib', '-framework', 'Foundation', tmp/'stand_in.m',
               '-o', tahoe)
            sh('codesign', '-f', '-s', '-', tahoe)
            compat = MAKE['build_compat'](tmp, tahoe)
            shutil.copy(compat, folder/'CoreWiFi')
            libs = sh('otool', '-L', folder/'CoreWiFi')
            self.assertIn('CoreWiFiTahoe.dylib (compatibility version', libs)
            self.assertIn('reexport', libs)
            out = run_probe(compile_probe(tmp), tmp/'frameworks')
            self.assertEqual(out.returncode, 0, out.stdout + out.stderr[-2000:])
            lines = out.stdout.splitlines()
            self.assertEqual(lines[0], 'key=LinkQualityAssessment consent=NSObject added=15 '
                                       'interface_image=CoreWiFiTahoe.dylib')
            # isConnectivityAssistEnabled keeps the stand-in's own YES: the table never replaces a method
            self.assertEqual(lines[1], 'pid=0 outcome=0 duration=0 carplay=0 assist=1 mrsno=0 rsn=0 dict=0/0')
            self.assertEqual(lines[2], 'coex=0/45 scan=45/0')
            self.assertEqual(lines[3], 'activate=1/0')


ARTIFACT = os.environ.get('COREWIFI_ARTIFACT')


def macos_major():
    return int(platform.mac_ver()[0].split('.')[0] or 0)


@unittest.skipUnless(ARTIFACT, 'set COREWIFI_ARTIFACT to a corewifi-tahoe artifact folder')
@unittest.skipUnless(platform.machine() == 'arm64' and macos_major() >= 27, 'needs Rosetta on a macOS 27 host')
class Artifact(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix='corewifi-artifact-test-'))
        folder = cls.tmp/'frameworks/CoreWiFi.framework/Versions/A'
        folder.mkdir(parents=True)
        for name in ('CoreWiFi', 'CoreWiFiTahoe.dylib'):
            shutil.copy(Path(ARTIFACT)/name, folder/name)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp)

    def test_override_with_cached_clients(self):
        out = run_probe(compile_probe(self.tmp), self.tmp/'frameworks',
                        '/System/Library/Frameworks/CoreWLAN.framework/Versions/A/CoreWLAN')
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr[-3000:])
        self.assertNotIn('is implemented in both', out.stderr)
        lines = out.stdout.splitlines()
        self.assertEqual(lines[0], 'key=LinkQualityAssessment consent=NSObject added=16 '
                                   'interface_image=CoreWiFiTahoe.dylib')
        self.assertEqual(lines[1], 'pid=0 outcome=0 duration=0 carplay=0 assist=0 mrsno=0 rsn=0 dict=0/0')
        self.assertEqual(lines[2], 'coex=0/45 scan=45/0')
        self.assertEqual(lines[3], 'responds=2')

    def test_swift_conformance_scan(self):
        (self.tmp/'probe.swift').write_text(SWIFT)
        sh('xcrun', 'swiftc', '-target', 'x86_64-apple-macos14', self.tmp/'probe.swift', '-o', self.tmp/'swiftprobe')
        sh('codesign', '-f', '-s', '-', self.tmp/'swiftprobe')
        out = run_probe(self.tmp/'swiftprobe', self.tmp/'frameworks')
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr[-3000:])
        self.assertEqual(out.stdout.strip(), 'swift type=true hashable=true int=true')


if __name__ == '__main__':
    unittest.main()
