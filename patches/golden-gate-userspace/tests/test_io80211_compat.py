"""Host tests for the IO80211 compat library (src/io80211): it exports exactly the 7 functions and re-exports
IO80211OCLP.dylib; under Rosetta it matches 26A428's x86_64 IO80211 at both ends and the middle of every run of the
full-range sweep in io80211-expected-26A428-x86_64.txt; macho.set_install_name changes only LC_ID_DYLIB. With
IO80211_OCLP_DIR set to a folder holding OCLP's IO80211 and IO80211Old.dylib (PatcherSupportPkg 13.7.2-25), every
IO80211 name resolves as before except the 7.
Run: python3.14 -I -m unittest discover -s patches/golden-gate-userspace/tests -p 'test_io80211*'"""
import importlib.util
import os
from pathlib import Path
import platform
import runpy
import shutil
import subprocess
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
SRC = HERE.parent/'src'
MAKE = runpy.run_path(str(SRC/'io80211/make'))
spec = importlib.util.spec_from_file_location('macho_io80211', SRC/'macho.py')
macho = importlib.util.module_from_spec(spec)
spec.loader.exec_module(macho)
FRAMEWORK = '/System/Library/PrivateFrameworks/IO80211.framework/Versions/A'
SEVEN = ['apple80211_apmodetoStr', 'apple80211_authtype_uppertoStr', 'apple80211_roam_profile_typetoStr',
         'apple80211_roam_status_bss_flagstoStr', 'convertAssocStatusToAssocResult', 'convertPhyTypeToString',
         'convertToApple80211Error']


def sh(*args, **kw):
    return subprocess.run(list(map(str, args)), check=True, capture_output=True, text=True, **kw).stdout


def x86(tmp, source, out, *flags):
    sh('xcrun', 'clang', '-arch', 'x86_64', '-O2', '-Wall', '-Wextra', '-Werror', *flags, source, '-o', tmp/out)
    sh('codesign', '-f', '-s', '-', tmp/out)
    return tmp/out


def stand_in(tmp, name, install_name):
    (tmp/'empty.c').write_text('int io80211_compat_test_stand_in;\n')
    return x86(tmp, tmp/'empty.c', name, '-dynamiclib', '-install_name', install_name)


def renamed(data, old, new):
    data = bytearray(data)
    macho.set_install_name(data, old.encode(), new.encode())
    return bytes(data)


class Library(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix='io80211-compat-test-'))
        cls.lib = MAKE['build'](cls.tmp)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp)

    def test_exports_and_load_commands(self):
        exports = [line.split()[-1] for line in sh('dyld_info', '-exports', self.lib).splitlines()
                   if line.strip().startswith('0x')]
        self.assertEqual(sorted(exports), sorted('_'+n for n in SEVEN))
        libs = sh('otool', '-L', self.lib)
        self.assertIn(f'{FRAMEWORK}/IO80211OCLP.dylib (compatibility version 1.0.0, current version 1.0.0, reexport)', libs)
        self.assertEqual(sh('otool', '-D', self.lib).splitlines()[-1], f'{FRAMEWORK}/IO80211')
        segments = [line.split()[-1] for line in sh('otool', '-l', self.lib).splitlines() if 'segname' in line]
        self.assertNotIn('__DATA', segments)   # string literals and jump tables only: nothing to rebase or bind

    @unittest.skipUnless(platform.machine() == 'arm64', 'runs the x86_64 library under Rosetta')
    def test_matches_26A428_over_every_run(self):
        folder = self.tmp/'reexport'
        folder.mkdir()
        stand_in(folder, 'IO80211OCLP.dylib', f'{FRAMEWORK}/IO80211OCLP.dylib')
        sweep = x86(self.tmp, HERE/'io80211_sweep.c', 'sweep')
        out = subprocess.run([sweep, 'check', self.lib, HERE/'io80211-expected-26A428-x86_64.txt'], capture_output=True,
                             text=True, env={**os.environ, 'DYLD_LIBRARY_PATH': str(folder)})
        self.assertEqual(out.returncode, 0, out.stdout+out.stderr)
        self.assertRegex(out.stdout, r'checked \d+ points, 0 mismatches')

    @unittest.skipUnless(os.environ.get('IO80211_OCLP_DIR'),
                         "set IO80211_OCLP_DIR to a folder with OCLP's IO80211 and IO80211Old.dylib")
    def test_every_other_name_resolves_as_before(self):
        oclp = Path(os.environ['IO80211_OCLP_DIR'])
        before, after = self.tmp/'before', self.tmp/'after'
        for d in (before, after):
            d.mkdir()
            shutil.copy(oclp/'IO80211Old.dylib', d)
        shutil.copy(oclp/'IO80211', before)
        (after/'IO80211OCLP.dylib').write_bytes(
            renamed((oclp/'IO80211').read_bytes(), f'{FRAMEWORK}/IO80211', f'{FRAMEWORK}/IO80211OCLP.dylib'))
        sh('codesign', '-f', '-s', '-', after/'IO80211OCLP.dylib')
        self.assertEqual(sh('dyld_info', '-exports', oclp/'IO80211').splitlines()[1:],
                         sh('dyld_info', '-exports', after/'IO80211OCLP.dylib').splitlines()[1:])
        shutil.copy(self.lib, after/'IO80211')
        names = set()
        for f in (oclp/'IO80211', oclp/'IO80211Old.dylib', self.lib):
            names |= {line.split()[-1][1:] for line in sh('nm', '-gU', f).splitlines() if line.strip()}
        (self.tmp/'names').write_text('\n'.join(sorted(names))+'\n')
        resolve = x86(self.tmp, HERE/'io80211_resolve.c', 'resolve')
        got = {}
        for d in (before, after):
            out = sh(resolve, d/'IO80211', self.tmp/'names', env={**os.environ, 'DYLD_LIBRARY_PATH': str(d)})
            got[d] = {line.split()[0]: line.split()[1:] for line in out.splitlines()}
        for name, where in got[before].items():
            if name in SEVEN:
                self.assertEqual(where[0], 'IO80211')   # before: the wrapper's data variable
                self.assertEqual(got[after][name][0], 'IO80211')
                self.assertNotEqual(got[after][name][1], where[1])
            else:
                self.assertEqual(got[after][name], ['IO80211OCLP.dylib', where[1]] if where[0] == 'IO80211' else where,
                                 name)


class InstallName(unittest.TestCase):
    def test_grows_the_id_into_the_padding(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            lib = stand_in(tmp, 'lib.dylib', '/x/IO80211')
            data = lib.read_bytes()
            new = '/x/'+'IO80211OCLP'*4+'.dylib'
            out = renamed(data, '/x/IO80211', new)
            self.assertEqual(len(out), len(data))
            (tmp/'renamed.dylib').write_bytes(out)
            self.assertEqual(sh('otool', '-D', tmp/'renamed.dylib').splitlines()[-1], new)
            self.assertEqual(sh('dyld_info', '-exports', lib).splitlines()[1:],
                             sh('dyld_info', '-exports', tmp/'renamed.dylib').splitlines()[1:])
            sh('codesign', '-f', '-s', '-', tmp/'renamed.dylib')
            sh('codesign', '--verify', tmp/'renamed.dylib')
            with self.assertRaisesRegex(ValueError, 'expected /x/other'):
                renamed(data, '/x/other', new)
            with self.assertRaisesRegex(ValueError, 'no room'):
                renamed(data, '/x/IO80211', '/x/'+'y'*40000)


if __name__ == '__main__':
    unittest.main()
