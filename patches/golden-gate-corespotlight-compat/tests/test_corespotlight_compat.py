"""Host tests: the shim adds the arm64e getter to x86_64 CoreSpotlight (run under Rosetta, whose x86_64 CoreSpotlight
lacks it like 26A428's), returns the ivar without a configuration, and add_load_dylib edits only the x86_64 slice.
Run: python3.14 -I -m unittest discover -s patches/golden-gate-corespotlight-compat/tests"""
from pathlib import Path
import platform
import os
import runpy
import subprocess
import tempfile
import unittest

HERE = Path(__file__).resolve().parents[1]
TOOL = runpy.run_path(str(HERE/'src/add_load_dylib.py'))
PROBE = r'''
#import <Foundation/Foundation.h>
#import <objc/runtime.h>
#include <dlfcn.h>
int main(int argc, char **argv) { @autoreleasepool {
    Class c = objc_lookUpClass("CSSearchQueryContext");
    BOOL before = class_getInstanceMethod(c, @selector(compiledRankingFactsConfiguration)) != NULL;
    if (argc > 1 && !dlopen(argv[1], RTLD_NOW)) { printf("dlopen failed %s\n", dlerror()); return 2; }
    BOOL after = class_getInstanceMethod(c, @selector(compiledRankingFactsConfiguration)) != NULL;
    id ctx = [[c alloc] init];
    id fresh = after ? [ctx performSelector:@selector(compiledRankingFactsConfiguration)] : @"-";
    Ivar ivar = class_getInstanceVariable(c, "_compiledRankingFactsConfiguration");
    object_setIvar(ctx, ivar, @{@"k": @1});
    id stored = after ? [ctx performSelector:@selector(compiledRankingFactsConfiguration)] : @"-";
    printf("before=%d after=%d fresh=%s stored=%s\n", before, after, fresh ? "obj" : "nil", [stored isEqual:@{@"k": @1}] ? "ivar" : "other");
    return 0;
}}
'''


@unittest.skipUnless(platform.machine() == 'arm64', 'needs Rosetta for an x86_64 CoreSpotlight without the getter')
class Shim(unittest.TestCase):
    def test_adds_getter_and_returns_ivar(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            dylib, probe = tmp/'corespotlight_compat.dylib', tmp/'probe'
            subprocess.run(['xcrun', 'clang', '-arch', 'x86_64', '-fobjc-arc', '-dynamiclib', '-framework', 'Foundation',
                            '-framework', 'CoreSpotlight', str(HERE/'src/corespotlight_compat.m'), '-o', str(dylib)], check=True)
            (tmp/'probe.m').write_text(PROBE)
            subprocess.run(['xcrun', 'clang', '-arch', 'x86_64', '-fobjc-arc', '-framework', 'Foundation',
                            '-framework', 'CoreSpotlight', str(tmp/'probe.m'), '-o', str(probe)], check=True)
            for f in (dylib, probe):
                subprocess.run(['codesign', '-f', '-s', '-', str(f)], check=True, capture_output=True)
            plain = subprocess.run(['arch', '-x86_64', str(probe)], capture_output=True, text=True).stdout
            if 'before=1' in plain:
                self.skipTest('this host x86_64 CoreSpotlight already has the getter')
            self.assertIn('before=0 after=0', plain)
            out = subprocess.run(['arch', '-x86_64', str(probe), str(dylib)], capture_output=True, text=True).stdout
            self.assertIn('before=0 after=1 fresh=nil stored=ivar', out)


class LoadCommand(unittest.TestCase):
    def test_adds_one_command_to_the_x86_64_slice_only(self):
        self.check_executable("corespotlightd")
        self.check_executable("managedcorespotlightd")

    def check_executable(self, name):
        source = Path('/System/Library/Frameworks/CoreServices.framework/Versions/A/Frameworks/Metadata.framework/'
                      'Versions/A/Support') / name
        donor = os.environ.get('CORESPOTLIGHT_SYSTEM')
        if not donor:
            self.skipTest('set CORESPOTLIGHT_SYSTEM to the supplied 26A428 System root')
        target = Path(donor)/source.relative_to('/')
        if not target.exists():
            self.fail('supplied System root lacks ' + str(source))
        data = target.read_bytes()
        out = TOOL['add'](data, '/usr/lib/corespotlight_compat.dylib')
        self.assertEqual(len(out), len(data))
        x86 = TOOL['slices'](data)[0]
        changed = [i for i in range(len(data)) if data[i] != out[i]]
        self.assertTrue(changed and all(x86 <= i < x86+0x4000 for i in changed))
        with self.assertRaisesRegex(ValueError, 'already referenced'):
            TOOL['add'](out, '/usr/lib/corespotlight_compat.dylib')
        with tempfile.TemporaryDirectory() as tmp:
            edited = Path(tmp)/'corespotlightd'
            edited.write_bytes(out)
            libs = subprocess.run(['otool', '-arch', 'x86_64', '-L', str(edited)], capture_output=True, text=True).stdout
            self.assertIn('/usr/lib/corespotlight_compat.dylib', libs)
            arm = subprocess.run(['otool', '-arch', 'arm64e', '-L', str(edited)], capture_output=True, text=True).stdout
            self.assertNotIn('corespotlight_compat', arm)


if __name__ == '__main__':
    unittest.main()
