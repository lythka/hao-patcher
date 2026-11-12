"""hao's Metal wrapper: generated from the SDK, built x86_64, loaded and exercised under Rosetta.
python3.14 -I -B -m unittest discover -s tools/metal-wrapper/tests

Needs Xcode with a macOS 27 SDK and Rosetta; skips otherwise. MetalOld is a stand-in built here with
MetalOld's install name (DYLD_LIBRARY_PATH points the re-export at it), so no Apple binary is used.

With the real binaries (never in git) two more checks run:
  METAL_WRAPPER_METALOLD=PATH/MetalOld.dylib   the static ObjC reader against MetalOld's own ivar symbols
  METAL_WRAPPER_BUILT=DIR/Metal (with the above) coverage: the built wrapper plus MetalOld export every symbol the
                                               gap table requires, except the documented exclusions and refusals"""
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

// What src/private.m builds on, under MetalOld's own names (the wrapper binds them to this image by symbol).
@interface _MTLObjectWithLabel : NSObject { NSString *_label; }
@property (copy) NSString *label;
@end
@implementation _MTLObjectWithLabel
@synthesize label = _label;
@end
@interface MTLFunctionStitchingFunctionNode : NSObject
@property (copy) NSString *name;
@property (copy) NSArray *arguments, *controlDependencies;
@end
@implementation MTLFunctionStitchingFunctionNode
- (instancetype)initWithName:(NSString *)n arguments:(NSArray *)a controlDependencies:(NSArray *)d {
    if ((self = [super init])) { self.name = n; self.arguments = a; self.controlDependencies = d; }
    return self;
}
@end
int hao_pushes;
char hao_last_push[256];
void _MTLMessageContextPush_(void *context, int level, NSString *format, ...) {
    va_list ap;
    va_start(ap, format);
    NSString *s = [[NSString alloc] initWithFormat:format arguments:ap];
    va_end(ap);
    hao_pushes++;
    snprintf(hao_last_push, sizeof hao_last_push, "%d %s", level, s.UTF8String);
}
// A MetalOld class with an ivar whose offset symbol it does not export (as MetalOld's private externs).
@interface HaoOldIvars : NSObject @end
@implementation HaoOldIvars { long _a; int _b; }
@end
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


# src/private.m, taken by symbol from our image (the host's own Metal defines the same class names).
PRIVATE = r'''
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#import <dlfcn.h>
#import <objc/message.h>
#import <objc/runtime.h>
typedef struct { uint64_t start, end; } R;
typedef struct { R *ranges; uint32_t count; uint64_t capacity, increment, alignmentMask; } RA;
typedef struct { uint64_t w[4]; } U256;
@interface FakeExtents : NSObject @end
@implementation FakeExtents
- (NSUInteger)rank { return 3; }
- (NSInteger)extentAtDimensionIndex:(NSUInteger)i { return (NSInteger)i + 2; }
@end
static const char *image(Class c) { return strstr(class_getImageName(c), "build/Metal") ? "ours"
                                          : strstr(class_getImageName(c), "old/MetalOld") ? "old" : "other"; }
static long ivar_ok(void *h, Class c, const char *ivar) {
    char sym[256];
    snprintf(sym, sizeof sym, "OBJC_IVAR_$_%s.%s", class_getName(c), ivar);
    Ivar v = class_getInstanceVariable(c, ivar);              // realizes the class: offsets are final now
    long *slot = dlsym(h, sym);
    return slot && v && *slot == ivar_getOffset(v);
}
int main(int argc, char **argv) {
    @autoreleasepool {
        void *h = dlopen(argv[1], RTLD_NOW);
        if (!h) { printf("dlopen: %s\n", dlerror()); return 1; }
        NSString *(*dtype)(long) = dlsym(h, "_MTLTensorDataTypeToString");
        NSString *(*plane)(unsigned long) = dlsym(h, "_MTLTensorPlaneTypeToString");
        uint64_t (*bits)(long) = dlsym(h, "tensorDataTypeBitCount");
        BOOL (*packed)(long) = dlsym(h, "_MTLTensorIsPackedFormatType");
        bool (*rgb)(unsigned long) = dlsym(h, "isRGBPixelFormat");
        NSString *(*page)(long) = dlsym(h, "MTLSparsePageSizeString");
        NSUInteger (*count)(id) = dlsym(h, "_MTLTensorElementCount");
        BOOL (*same)(id, id) = dlsym(h, "_MTLTensorExtentsAreEqual");
        printf("dtype=%s,%s,%s plane=%s,%s\n", dtype(3).UTF8String, dtype(145).UTF8String, dtype(7).UTF8String,
               plane(1).UTF8String, plane(5).UTF8String);
        printf("bits=%llu,%llu,%llu,%llu,%llu packed=%d,%d\n", bits(3), bits(121), bits(146), bits(149), bits(7),
               packed(143), packed(49));
        printf("rgb=%d%d%d%d%d page=%s,%s\n", rgb(45), rgb(97), rgb(121), rgb(101), rgb(70), page(102).UTF8String,
               page(9).UTF8String);
        FakeExtents *e = [FakeExtents new];
        printf("elements=%lu same=%d\n", (unsigned long)count(e), same(e, [FakeExtents new]));

        void (*init)(RA *, uint64_t, uint64_t, uint64_t, uint64_t) = dlsym(h, "MTLRangeAllocatorInitWithStartRange");
        uint64_t (*freesize)(RA *) = dlsym(h, "MTLRangeAllocatorGetFreeSize");
        RA a;
        init(&a, 0x1000, 0x100, 16, 0);
        printf("range=%u %llx-%llx mask=%llu inc=%llu free=%llx\n", a.count, a.ranges[0].start, a.ranges[0].end,
               a.alignmentMask, a.increment, freesize(&a));
        free(a.ranges);

        void (*rtptg)(void *, MTLSize, MTLSize) = dlsym(h, "validateDispatchThreadsPerThreadgroupWithRTPTG");
        int *pushes = dlsym(RTLD_DEFAULT, "hao_pushes");
        rtptg(NULL, MTLSizeMake(8, 8, 1), MTLSizeMake(0, 0, 0));
        int before = *pushes;
        rtptg(NULL, MTLSizeMake(8, 8, 1), MTLSizeMake(8, 4, 1));
        printf("rtptg=%d,%d %s\n", before, *pushes, (char *)dlsym(RTLD_DEFAULT, "hao_last_push"));

        U256 (*hash)(id) = dlsym(h, "_Z34generatePipelineHashWithDescriptorP22MTL4PipelineDescriptor");
        U256 r = hash(nil);
        NSString **pattern = dlsym(h, "kMetalTextureWriteAccessPattern");
        printf("hash=%llx pattern=%s internal=%d\n", r.w[0] | r.w[1] | r.w[2] | r.w[3], (*pattern).UTF8String,
               ((BOOL (*)(void))dlsym(h, "_MTLIsInternalBuild"))());

        Class decoder = dlsym(h, "OBJC_CLASS_$_MTLMessageDecoder");
        id d = ((id (*)(id, SEL, const char *, const void *, unsigned, id))objc_msgSend)([decoder alloc],
            sel_registerName("initWithFormatString:varArgsData:varArgsSize:logger:"), "x=%d", NULL, 0, nil);
        printf("decoder=%s decode=%p\n", image(decoder), [d performSelector:sel_registerName("decode")]);

        // IOGPU-style: subclass the Metal 4 command buffer, then read its ivars through our offset symbols.
        Class cb = dlsym(h, "OBJC_CLASS_$__MTL4CommandBuffer");
        Class sub = objc_allocateClassPair(cb, "HaoIOGPUCommandBuffer", 0);
        class_addIvar(sub, "_extra", 8, 3, "Q");
        objc_registerClassPair(sub);
        id dev = [NSObject new];
        id b = ((id (*)(id, SEL, id))objc_msgSend)([sub alloc], sel_registerName("initWithDevice:"), dev);
        [b setValue:@"cb" forKey:@"label"];
        long *devoff = dlsym(h, "OBJC_IVAR_$__MTL4CommandBuffer._device");
        printf("cb=%s super=%s(%s) device=%d label=%s ivars=%ld%ld%ld\n", image(cb), class_getName(class_getSuperclass(cb)),
               image(class_getSuperclass(cb)), *(id *)((char *)b + *devoff) == dev, [[b valueForKey:@"label"] UTF8String],
               ivar_ok(h, cb, "_device"), ivar_ok(h, cb, "_labelTraceID"), ivar_ok(h, cb, "_commandAllocator"));
        Class compute = dlsym(h, "OBJC_CLASS_$__MTL4ComputeCommandEncoder");
        Class alloc = dlsym(h, "OBJC_CLASS_$__MTLAllocation");
        printf("compute=%s>%s allocation=%ld%s\n", class_getName(class_getSuperclass(compute)),
               class_getName(class_getSuperclass(class_getSuperclass(compute))), ivar_ok(h, alloc, "_allocationType"),
               ivar_getTypeEncoding(class_getInstanceVariable(alloc, "_allocationType")));

        Class early = dlsym(h, "OBJC_CLASS_$_MTLFunctionStitchingEarlyReturnNode");
        id cond = [NSObject new];
        id node = ((id (*)(id, SEL, id, id))objc_msgSend)([early alloc],
            sel_registerName("initWithCondition:controlDependencies:"), cond, @[]);
        id copy = [node copy];
        printf("early=%s(%s) condition=%d copy=%d name='%s'\n", class_getName(class_getSuperclass(early)),
               image(class_getSuperclass(early)), [node performSelector:sel_registerName("condition")] == cond,
               [copy class] == early && [copy performSelector:sel_registerName("condition")] == cond,
               [[node valueForKey:@"name"] UTF8String]);
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
                    'functions': [], 'data': ['MTLLogStateErrorDomain'],
                    'ivars': ['_OBJC_IVAR_$_HaoOldIvars._b', '_OBJC_IVAR_$_HaoOldIvars._missing',
                              '_OBJC_IVAR_$__MTL4CommandBuffer._labelTraceID']}
        cls.report = MAKE['build'](cls.spec, old / 'MetalOld.dylib', cls.d / 'build',
                                  defines=['HAO_METAL_CLASS_PREFIX="Hao"'])
        for name, text in (('p', PROGRAM), ('priv', PRIVATE)):
            (cls.d / (name + '.m')).write_text(text)
            clang('-fno-objc-arc', '-framework', 'Foundation', cls.d / (name + '.m'),
                  '-o', cls.d / name)
            subprocess.run(['codesign', '-f', '-s', '-', cls.d / name], check=True, capture_output=True)
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

    def run_private(self):
        r = subprocess.run([str(self.d / 'priv'), str(self.d / 'build/Metal')], capture_output=True, text=True,
                           env=dict(os.environ, DYLD_LIBRARY_PATH=str(self.old)))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return r.stdout

    def test_private_functions(self):
        out = self.run_private()
        self.assertIn('dtype=MTLTensorDataTypeFloat32,MTLTensorDataTypeMetalFloat8UE8M0,Unknown '
                      'plane=MTLTensorPlaneTypeScales,Unknown', out)
        self.assertIn('bits=32,16,6,2,0 packed=1,0', out)
        self.assertIn('rgb=11100 page=MTLSparsePageSize64,Unknown', out)
        self.assertIn('elements=24 same=1', out)
        self.assertIn('range=1 1000-1100 mask=15 inc=1 free=101', out)
        # no required size: no message; width matches, height doesn't: one error (level 8), 27.2.1's wording
        self.assertIn('rtptg=0,1 8 threadsPerThreadgroup.height(8) should be 4 (requiredThreadsPerThreadgroup.height)',
                      out)
        self.assertIn('hash=0 pattern=kMetalTextureWriteAccessPattern internal=0', out)

    def test_private_classes(self):
        out = self.run_private()
        self.assertIn('decoder=ours decode=0x0', out)         # MetalTools then shows the undecoded format string
        self.assertIn('cb=ours super=_MTLObjectWithLabel(old) device=1 label=cb ivars=111', out)
        self.assertIn('compute=_MTL4CommandEncoder>_MTLObjectWithLabel allocation=1c', out)
        self.assertIn("early=MTLFunctionStitchingFunctionNode(old) condition=1 copy=1 name=''", out)

    def test_ivar_offsets(self):
        # MetalOld's class has _b: exported at MetalOld's offset. It has no _missing: refused, not faked. Our own
        # class's ivar comes from its declaration.
        self.assertEqual(self.report['ivars_metalold'][0]['symbol'], '_OBJC_IVAR_$_HaoOldIvars._b')
        self.assertEqual([r['symbol'] for r in self.report['refused']], ['_OBJC_IVAR_$_HaoOldIvars._missing'])
        self.assertEqual(self.report['ivars_ours'], ['_OBJC_IVAR_$__MTL4CommandBuffer._labelTraceID'])
        _, ours = MAKE['exported'](self.d / 'build/Metal')
        self.assertIn('_OBJC_IVAR_$_HaoOldIvars._b', ours)
        self.assertNotIn('_OBJC_IVAR_$_HaoOldIvars._missing', ours)
        static = MAKE['objc_classes'](self.old / 'MetalOld.dylib')['HaoOldIvars']['ivars']['_b']['offset']
        self.assertEqual(self.report['ivars_metalold'][0]['offset'], static)
        prog = self.d / 'iv.m'
        prog.write_text(IVARS)
        clang('-fno-objc-arc', '-framework', 'Foundation', prog, '-o', self.d / 'iv')
        subprocess.run(['codesign', '-f', '-s', '-', self.d / 'iv'], check=True, capture_output=True)
        r = subprocess.run([str(self.d / 'iv'), str(self.d / 'build/Metal')], capture_output=True, text=True,
                           env=dict(os.environ, DYLD_LIBRARY_PATH=str(self.old)))
        self.assertEqual(r.stdout.strip(), 'b=%d runtime=%d' % (static, static), r.stderr)

    def test_names_metalold_has_are_skipped(self):
        src, report = MAKE['generate']({'classes': ['MTLResidencySetDescriptor'], 'data': ['metalold_marker']},
                                       metalold=self.old / 'MetalOld.dylib', scratch=str(self.d))
        self.assertEqual(report['skipped'], ['metalold_marker'])
        self.assertIn('@implementation MTLResidencySetDescriptor', src)


IVARS = r"""
#import <Foundation/Foundation.h>
#import <dlfcn.h>
#import <objc/runtime.h>
int main(int argc, char **argv) {
    void *h = dlopen(argv[1], RTLD_NOW);
    long *b = dlsym(h, "OBJC_IVAR_$_HaoOldIvars._b");
    printf("b=%ld runtime=%td\n", b ? *b : -1, ivar_getOffset(class_getInstanceVariable(objc_getClass("HaoOldIvars"), "_b")));
    return 0;
}
"""

GAP = HERE.parent / 'spec' / 'gap-26B131.json'
# Used only by Apple-GPU driver bundles (AGXMetal*, never loaded on Intel) and not provided by Moraea's wrapper.
EXCLUDED = {'MTLCompileBuiltinFunctionRequestData', 'MTLGenericAddressSpaceDispatchInfo', '_MTL4DispatchGroupCompilerTask',
            '_MTLIndirectBlendColor', '_MTLIndirectDepthBiasInfo', '_MTLIndirectDepthTestBounds',
            '_MTLIndirectScissorRects', '_MTLIndirectStencilReferenceValues', '_MTLIndirectViewports',
            '_MTLTensorAuxiliaryPlane', '_MTLPipelinePerformanceKeyGASNativeInstructionCount',
            '_MTLPipelinePerformanceKeyNonSmashableAtomicInstructionCount',
            '_MTLPipelinePerformanceKeySmashableAtomicInstructionCount'}
# Ivars of MetalOld's own classes that MetalOld's classes don't have (see README).
REFUSED = {'_OBJC_IVAR_$__MTLCommandBuffer._privateData', '_OBJC_IVAR_$__MTLCommandBuffer._privateDataOffset',
           '_OBJC_IVAR_$__MTLCommandBuffer._requiresBindingLogState', '_OBJC_IVAR_$__MTLCommandQueue._privateDataTable'}


class GapSelection(unittest.TestCase):
    """What a build from the 26B131 gap table takes; reads only the JSON."""

    def test_counts(self):
        spec = MAKE['load_spec'](GAP)
        self.assertEqual((len(spec['classes']), len(spec['functions']), len(spec['data']), len(spec['ivars'])),
                         (64, 15, 4, 22))
        self.assertEqual(set(spec['excluded']), EXCLUDED)
        gap = json.loads(GAP.read_text())
        missing = [s for s in gap['symbols'] if s['required'] and not s['exported_by']['metalold']]
        moraea = [s for s in missing if s['exported_by']['moraea_wrapper']]
        self.assertEqual((len(missing), len(moraea)), (118, 90))
        taken = {s['name'] for s in missing} - EXCLUDED
        self.assertTrue({s['name'] for s in moraea} <= taken)
        self.assertEqual(len(taken), 105)


METALOLD = os.environ.get('METAL_WRAPPER_METALOLD')
BUILT = os.environ.get('METAL_WRAPPER_BUILT')


@unittest.skipUnless(METALOLD, 'set METAL_WRAPPER_METALOLD to MetalOld.dylib')
class RealMetalOld(unittest.TestCase):
    def test_static_ivar_reader(self):
        # every ivar-offset symbol MetalOld exports is where the reader finds that ivar's offset variable
        classes = MAKE['objc_classes'](METALOLD)
        out = subprocess.run(['nm', '-m', '--defined-only', METALOLD], capture_output=True, text=True).stdout
        seen = 0
        for line in out.splitlines():
            name = line.split()[-1]
            if name.startswith('_OBJC_IVAR_$_'):
                cls, ivar = MAKE['split_ivar'](name)
                self.assertEqual(classes[cls]['ivars'][ivar]['address'], int(line.split()[0], 16), name)
                seen += 1
        self.assertGreater(seen, 1000)
        for symbol in REFUSED:
            cls, ivar = MAKE['split_ivar'](symbol)
            self.assertIn(cls, classes)
            self.assertNotIn(ivar, classes[cls]['ivars'])

    @unittest.skipUnless(BUILT, 'set METAL_WRAPPER_BUILT to the built wrapper (make.py build ... --out DIR: DIR/Metal)')
    def test_coverage(self):
        # every symbol x86_64 code in 26B131 requires from Metal is exported by the wrapper or the MetalOld it
        # re-exports, except the documented exclusions (whole entries) and refusals (ivars MetalOld lacks)
        _, ours = MAKE['exported'](BUILT)
        _, old = MAKE['exported'](METALOLD)
        gap = json.loads(GAP.read_text())
        uncovered = set()
        for s in gap['symbols']:
            if s['required'] and s['name'] not in EXCLUDED:
                uncovered |= {sym for sym in s['symbols'] if sym not in ours and sym not in old}
        self.assertEqual(uncovered, REFUSED)
        self.assertEqual(ours & old, set())                       # nothing defined twice
        out = subprocess.run(['otool', '-l', BUILT], capture_output=True, text=True).stdout
        self.assertIn('LC_REEXPORT_DYLIB', out)
        sig = subprocess.run(['codesign', '-dv', BUILT], capture_output=True, text=True).stderr
        self.assertIn('Signature=adhoc', sig)


if __name__ == '__main__':
    unittest.main()
