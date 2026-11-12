"""hao's Metal wrapper: generated from the SDK, built x86_64, loaded and exercised under Rosetta.
python3.14 -I -B -m unittest discover -s tools/metal-wrapper/tests

Needs Xcode with a macOS 27 SDK and Rosetta; skips otherwise. MetalOld is a stand-in built here with
MetalOld's install name (DYLD_LIBRARY_PATH points the re-export at it), so no Apple binary is used."""
import json
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
MAKE = runpy.run_path(str(HERE.parent / 'make.py'))
METALOLD_ID = '/System/Library/Frameworks/Metal.framework/Versions/A/MetalOld.dylib'


def have_toolchain():
    try:
        sdk = MAKE['sdk_path']()
    except (OSError, subprocess.CalledProcessError):
        return False
    rosetta = subprocess.run(['arch', '-x86_64', '/usr/bin/true'], capture_output=True).returncode == 0
    return rosetta and (Path(sdk) / 'System/Library/Frameworks/Metal.framework/Headers/MTL4RenderPipeline.h').is_file()


def clang(*args):
    r = subprocess.run(['xcrun', 'clang', '-arch', 'x86_64', '-mmacosx-version-min=27.0', *map(str, args)],
                       capture_output=True, text=True)
    if r.returncode:
        raise AssertionError(r.stderr[-3000:])


# The stand-in MetalOld: the internal classes the wrapper's load-time workarounds touch, with MetalOld's answers.
OLD = r'''
#import <Foundation/Foundation.h>
int metalold_marker = 1;
@interface HaoMTLCompileOptionsInternal : NSObject @end
@implementation HaoMTLCompileOptionsInternal
- (void)setLanguageVersion:(unsigned long)v { abort(); }
@end
@interface HaoMTLHeapDescriptorInternal : NSObject @end
@implementation HaoMTLHeapDescriptorInternal @end
@interface HaoMTLCompiler : NSObject @end
@implementation HaoMTLCompiler
- (BOOL)validateLanguageAndAIRVersionForFunction:(id)f completionHandler:(id)h { return NO; }
@end
@interface Hao_MTLDevice : NSObject @end
@implementation Hao_MTLDevice
- (BOOL)supportsFamily:(long)family { return family == 1001; }
@end
@interface HaoMTLIOAccelDevice : Hao_MTLDevice @end
@implementation HaoMTLIOAccelDevice
- (unsigned long long)registryID { return 0x1234; }
@end
@interface HaoNSProcessInfo : NSObject @end
@implementation HaoNSProcessInfo @end
'''

LOADTIME = r'''
#import <Foundation/Foundation.h>
#import <dlfcn.h>
#import <objc/message.h>
int main(int argc, char **argv) {
    @autoreleasepool {
        if (!dlopen(argv[1], RTLD_NOW)) { printf("dlopen: %s\n", dlerror()); return 1; }
        id opts = [NSClassFromString(@"HaoMTLCompileOptionsInternal") new];
        ((void (*)(id, SEL, unsigned long))objc_msgSend)(opts, NSSelectorFromString(@"setLanguageVersion:"), 3);
        long math = ((long (*)(id, SEL))objc_msgSend)(opts, NSSelectorFromString(@"mathMode"));
        id comp = [NSClassFromString(@"HaoMTLCompiler") new];
        BOOL ok = ((BOOL (*)(id, SEL, id, id))objc_msgSend)(comp,
            NSSelectorFromString(@"validateLanguageAndAIRVersionForFunction:completionHandler:"), nil, nil);
        id dev = [NSClassFromString(@"HaoMTLIOAccelDevice") new];
        BOOL mac2 = ((BOOL (*)(id, SEL, long))objc_msgSend)(dev, NSSelectorFromString(@"supportsFamily:"), 2002);
        BOOL f1001 = ((BOOL (*)(id, SEL, long))objc_msgSend)(dev, NSSelectorFromString(@"supportsFamily:"), 1001);
        unsigned long long reg = ((unsigned long long (*)(id, SEL))objc_msgSend)(dev, NSSelectorFromString(@"registryID"));
        BOOL cert = ((BOOL (*)(id, SEL, long))objc_msgSend)([NSClassFromString(@"HaoNSProcessInfo") new],
            NSSelectorFromString(@"isDeviceCertifiedFor:"), 1);
        printf("math=%ld validate=%d mac2=%d f1001=%d registry=%llx cert=%d\n", math, ok, mac2, f1001, reg, cert);
    }
    return 0;
}
'''

PROGRAM = r'''
#import <Foundation/Foundation.h>
#import <dlfcn.h>
#import <objc/message.h>
int main(int argc, char **argv) {
    @autoreleasepool {
        void *h = dlopen(argv[1], RTLD_NOW);
        if (!h) { printf("dlopen: %s\n", dlerror()); return 1; }
        // the host's own Metal is loaded too and defines the same class names: take ours by symbol
        Class rs = (Class)dlsym(h, "OBJC_CLASS_$_MTLResidencySetDescriptor");
        printf("image=%s\n", strstr(class_getImageName(rs), "build/Metal") ? "ours" : class_getImageName(rs));
        id d = [[rs alloc] init];
        [d setValue:@"heap" forKey:@"label"];
        [d setValue:@64 forKey:@"initialCapacity"];
        id c = [d copy];
        printf("residency label=%s capacity=%lu copy=%s\n", [[d valueForKey:@"label"] UTF8String],
               (unsigned long)[[d valueForKey:@"initialCapacity"] unsignedLongValue], [[c valueForKey:@"label"] UTF8String]);
        Class rp = (Class)dlsym(h, "OBJC_CLASS_$_MTL4RenderPipelineDescriptor");
        id p = [[rp alloc] init];
        printf("render4 super=%s responds=%d\n", class_getName(class_getSuperclass(rp)),
               (int)[p respondsToSelector:NSSelectorFromString(@"reset")]);
        ((void (*)(id, SEL))objc_msgSend)(p, NSSelectorFromString(@"reset"));
        NSString *const *domain = (NSString *const *)dlsym(RTLD_DEFAULT, "MTLLogStateErrorDomain");
        printf("domain=%s old=%d\n", domain ? [*domain UTF8String] : "missing", dlsym(RTLD_DEFAULT, "metalold_marker") != 0);
    }
    return 0;
}
'''


@unittest.skipUnless(have_toolchain(), 'needs Xcode with the macOS 27 SDK and Rosetta')
class Wrapper(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = Path(tempfile.mkdtemp())
        old = cls.d / 'old'; old.mkdir()
        (cls.d / 'old.m').write_text(OLD)
        clang('-dynamiclib', '-fno-objc-arc', '-framework', 'Foundation', '-install_name', METALOLD_ID,
              cls.d / 'old.m', '-o', old / 'MetalOld.dylib')
        subprocess.run(['codesign', '-f', '-s', '-', old / 'MetalOld.dylib'], check=True, capture_output=True)
        cls.spec = {'classes': ['MTLResidencySetDescriptor', 'MTL4PipelineDescriptor', 'MTL4RenderPipelineDescriptor'],
                    'functions': [], 'data': ['MTLLogStateErrorDomain']}
        cls.report = MAKE['build'](cls.spec, old / 'MetalOld.dylib', cls.d / 'build',
                                  defines=['HAO_METAL_CLASS_PREFIX="Hao"'])
        (cls.d / 'p.m').write_text(PROGRAM)
        clang('-fno-objc-arc', '-framework', 'Foundation', cls.d / 'p.m', '-o', cls.d / 'p')
        subprocess.run(['codesign', '-f', '-s', '-', cls.d / 'p'], check=True, capture_output=True)
        cls.old = old

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.d, ignore_errors=True)

    def test_shape(self):
        out = subprocess.run(['otool', '-l', str(self.d / 'build/Metal')], capture_output=True, text=True).stdout
        self.assertIn('LC_REEXPORT_DYLIB', out)
        self.assertIn(METALOLD_ID, out)
        ident = subprocess.run(['otool', '-D', str(self.d / 'build/Metal')], capture_output=True, text=True).stdout
        self.assertIn(MAKE['INSTALL_NAME'], ident)
        self.assertEqual(sorted(self.report['classes']), sorted(self.spec['classes']))

    def test_runs_under_rosetta(self):
        env = dict(os.environ, DYLD_LIBRARY_PATH=str(self.old))
        r = subprocess.run([str(self.d / 'p'), str(self.d / 'build/Metal')], capture_output=True,
                           text=True, env=env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn('image=ours', r.stdout)                       # our stubs, not the host's Metal
        self.assertIn('residency label=heap capacity=64 copy=heap', r.stdout)
        self.assertIn('render4 super=MTL4PipelineDescriptor responds=1', r.stdout)
        self.assertIn('domain=MTLLogStateErrorDomain old=1', r.stdout)   # MetalOld is re-exported

    def loadtime(self, name, **env):
        (self.d / 'lt.m').write_text(LOADTIME)
        clang('-fno-objc-arc', '-framework', 'Foundation', self.d / 'lt.m', '-o', self.d / name)
        subprocess.run(['codesign', '-f', '-s', '-', self.d / name], check=True, capture_output=True)
        r = subprocess.run([str(self.d / name), str(self.d / 'build/Metal')], capture_output=True, text=True,
                           env=dict(os.environ, DYLD_LIBRARY_PATH=str(self.old), **env))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return r.stdout.strip()

    def test_load_time_workarounds(self):
        # every process: the 20 missing selectors answer 0, Core Image's language checks pass, setLanguageVersion:
        # no longer reaches MetalOld (whose stand-in aborts)
        self.assertEqual(self.loadtime('plain'), 'math=0 validate=1 mac2=0 f1001=1 registry=1234 cert=0')
        # a WebKit process: Mac2 is reported, other families keep MetalOld's answer, registryID reads 0
        self.assertEqual(self.loadtime('com.apple.WebKit.test'), 'math=0 validate=1 mac2=1 f1001=1 registry=0 cert=0')

    def test_each_workaround_switches_off(self):
        out = self.loadtime('com.apple.WebKit.off', HAO_METAL_DISABLE='webgl')
        self.assertIn('mac2=0', out)
        self.assertIn('registry=1234', out)

    def test_names_metalold_has_are_skipped(self):
        src, report = MAKE['generate']({'classes': ['MTLResidencySetDescriptor'], 'data': ['metalold_marker']},
                                       metalold=self.old / 'MetalOld.dylib', scratch=str(self.d))
        self.assertEqual(report['skipped'], ['metalold_marker'])
        self.assertIn('@implementation MTLResidencySetDescriptor', src)


if __name__ == '__main__':
    unittest.main()
