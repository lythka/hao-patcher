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


PROGRAM = r'''
#import <Foundation/Foundation.h>
#import <dlfcn.h>
#import <objc/message.h>
int main(int argc, char **argv) {
    @autoreleasepool {
        if (!dlopen(argv[1], RTLD_NOW)) { printf("dlopen: %s\n", dlerror()); return 1; }
        Class rs = NSClassFromString(@"MTLResidencySetDescriptor");
        id d = [[rs alloc] init];
        [d setValue:@"heap" forKey:@"label"];
        [d setValue:@64 forKey:@"initialCapacity"];
        id c = [d copy];
        printf("residency label=%s capacity=%lu copy=%s\n", [[d valueForKey:@"label"] UTF8String],
               (unsigned long)[[d valueForKey:@"initialCapacity"] unsignedLongValue], [[c valueForKey:@"label"] UTF8String]);
        Class rp = NSClassFromString(@"MTL4RenderPipelineDescriptor");
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
        (cls.d / 'old.c').write_text('int metalold_marker = 1;\n')
        clang('-dynamiclib', '-install_name', METALOLD_ID, cls.d / 'old.c', '-o', old / 'MetalOld.dylib')
        subprocess.run(['codesign', '-f', '-s', '-', old / 'MetalOld.dylib'], check=True, capture_output=True)
        cls.spec = {'classes': ['MTLResidencySetDescriptor', 'MTL4PipelineDescriptor', 'MTL4RenderPipelineDescriptor'],
                    'functions': [], 'data': ['MTLLogStateErrorDomain']}
        cls.report = MAKE['build'](cls.spec, old / 'MetalOld.dylib', cls.d / 'build')
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
        self.assertIn('residency label=heap capacity=64 copy=heap', r.stdout)
        self.assertIn('render4 super=MTL4PipelineDescriptor responds=1', r.stdout)
        self.assertIn('domain=MTLLogStateErrorDomain old=1', r.stdout)   # MetalOld is re-exported

    def test_names_metalold_has_are_skipped(self):
        src, report = MAKE['generate']({'classes': ['MTLResidencySetDescriptor'], 'data': ['metalold_marker']},
                                       metalold=self.old / 'MetalOld.dylib', scratch=str(self.d))
        self.assertEqual(report['skipped'], ['metalold_marker'])
        self.assertIn('@implementation MTLResidencySetDescriptor', src)


if __name__ == '__main__':
    unittest.main()
