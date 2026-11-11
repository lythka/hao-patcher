"""Host tests for the IO80211 compat library (src/io80211): it exports exactly the 8 functions and re-exports
IO80211OCLP.dylib; under Rosetta it matches 26B131's x86_64 IO80211 at both ends and the middle of every run of the
full-range sweep in io80211-expected-26B131-x86_64.txt, and 26A428's except where 26B131 added a code;
the coverage check's resolution and kind rules; macho.set_install_name changes only LC_ID_DYLIB. With
IO80211_OCLP_DIR set to a folder holding OCLP's IO80211 and IO80211Old.dylib (PatcherSupportPkg 13.7.2-25), every
IO80211 name resolves as before except the 8.
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
ADDED = ['Apple80211Get20MhzChannelIndexBitmap']   # 27.x exports OCLP's wrapper lacks
FORWARDED = ['Apple80211StartMonitoringEvent']      # the wrapper's RTLD_NEXT forwarder, now looked up in IO80211Old
# Codes a later build's table adds, which an earlier build passes through: (build, function, input).
NEWER = {('26A428', 'convertToApple80211Error', 0xe0822410)}


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
        self.assertEqual(sorted(exports), sorted('_'+n for n in SEVEN+ADDED+FORWARDED))
        libs = sh('otool', '-L', self.lib)
        self.assertIn(f'{FRAMEWORK}/IO80211OCLP.dylib (compatibility version 1.0.0, current version 1.0.0, reexport)', libs)
        self.assertEqual(sh('otool', '-D', self.lib).splitlines()[-1], f'{FRAMEWORK}/IO80211')
        # every export is code (the forwarder's cached pointer and its dlopen/dlsym imports are the only data)
        self.assertEqual(set(coverage.exports(self.lib).values()), {'__TEXT'})

    @unittest.skipUnless(platform.machine() == 'arm64', 'runs the x86_64 library under Rosetta')
    def test_matches_every_build_over_every_run(self):
        folder = self.tmp/'reexport'
        folder.mkdir()
        stand_in(folder, 'IO80211OCLP.dylib', f'{FRAMEWORK}/IO80211OCLP.dylib')
        sweep = x86(self.tmp, HERE/'io80211_sweep.c', 'sweep')
        for expected in sorted(HERE.glob('io80211-expected-*-x86_64.txt')):
            build = expected.name.split('-')[2]
            out = subprocess.run([sweep, 'check', self.lib, expected], capture_output=True,
                                 text=True, env={**os.environ, 'DYLD_LIBRARY_PATH': str(folder)})
            found = {(build, line.split()[1], int(line.split()[5].rstrip(':'), 16))
                     for line in out.stdout.splitlines() if line.startswith('MISMATCH')}
            allowed = {n for n in NEWER if n[0] == build}
            self.assertEqual(found, allowed, out.stdout)
            self.assertRegex(out.stdout, rf'checked \d+ points, {len(allowed)} mismatches')

    def test_get20mhz_channel_bits(self):
        source = self.tmp/'g20.c'
        source.write_text('#include <dlfcn.h>\n#include <stdint.h>\n#include <stdio.h>\n'
                          'int main(int c, char **v) { (void)c; void *h = dlopen(v[1], RTLD_NOW); if (!h) return 2;\n'
                          '  uint64_t (*f)(uint32_t, uint32_t) = (uint64_t (*)(uint32_t, uint32_t))dlsym(h, '
                          '"Apple80211Get20MhzChannelIndexBitmap");\n'
                          '  uint32_t in[][2] = {{6,0xa},{6,0x8},{36,0x12},{165,0x12},{0x124,0x12},{37,0x2002},{233,0x2002},'
                          '{37,0x2},{70,0xa},{36,0x201a}};\n'
                          '  for (unsigned i = 0; i < sizeof in / sizeof *in; i++) printf("%llx\\n", (unsigned long long)f(in[i][0], in[i][1]));\n'
                          '  return 0; }\n')
        if platform.machine() != 'arm64':
            self.skipTest('runs the x86_64 library under Rosetta')
        folder = self.tmp/'reexport-g20'
        folder.mkdir()
        stand_in(folder, 'IO80211OCLP.dylib', f'{FRAMEWORK}/IO80211OCLP.dylib')
        probe = x86(self.tmp, source, 'g20')
        out = sh(probe, self.lib, env={**os.environ, 'DYLD_LIBRARY_PATH': str(folder)}).split()
        # 2.4 GHz bit N; 5 GHz (N+24)/4 of the low byte; 6 GHz N/4 of the low byte; 0 without 20 MHz; 2.4 GHz first.
        # Values read from 26B131's own function (raw-mapped __TEXT) for the same inputs.
        self.assertEqual(out, ['40', '0', '8000', '800000000000', '8000', '200', '400000000000000', '0',
                               '40', '1000000000'])

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
            elif name in FORWARDED:
                self.assertEqual(where[0], 'IO80211')   # before: the wrapper's RTLD_NEXT forwarder
                self.assertEqual(got[after][name][0], 'IO80211')
                self.assertNotEqual(got[after][name][1], where[1])
            elif name in ADDED:
                self.assertEqual(where, ['-'])   # before: unresolved
                self.assertEqual(got[after][name][0], 'IO80211')
            else:
                self.assertEqual(got[after][name], ['IO80211OCLP.dylib', where[1]] if where[0] == 'IO80211' else where,
                                 name)


spec = importlib.util.spec_from_file_location('io80211_coverage', SRC/'io80211/coverage.py')
coverage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(coverage)


class Coverage(unittest.TestCase):
    PROVIDERS = [('compat', {'_fixed': '__TEXT'}),
                 ('OCLP IO80211', {'_fixed': '__DATA', '_asdata': '__DATA', '_wrapped': '__TEXT'}),
                 ('OCLP IO80211Old.dylib', {'_old': '__TEXT', '_wrapped': '__TEXT', '__pid': '__DATA'})]
    KINDS = {'_fixed': 'code', '_asdata': 'code', '_wrapped': 'code', '_old': 'code', '__pid': 'data',
             '_gone': 'code'}

    def test_first_provider_wins_and_data_is_data(self):
        resolved, errors = coverage.assess({'_fixed': {'shared cache'}, '_wrapped': {'x'}, '_old': {'x'},
                                            '__pid': {'x'}}, self.KINDS, self.PROVIDERS)
        self.assertEqual(errors, [])
        self.assertEqual(resolved, {'_fixed': 'compat', '_wrapped': 'OCLP IO80211', '_old': 'OCLP IO80211Old.dylib',
                                    '__pid': 'OCLP IO80211Old.dylib'})

    def test_missing_and_code_as_data_fail(self):
        _, errors = coverage.assess({'_gone': {'shared cache'}, '_asdata': {'usr/libexec/airportd [arm64e]'}},
                                    self.KINDS, self.PROVIDERS)
        self.assertEqual(len(errors), 2)
        self.assertRegex(errors[0], r'^MISSING/unsupported: _asdata .* exports it from __DATA')
        self.assertRegex(errors[1], r'^MISSING/unsupported: _gone \(bound by shared cache\) is provided by neither')

    def test_weak_only_may_stay_unresolved(self):
        resolved, errors = coverage.assess({'_gone': {'a [x86_64] (weak)'}}, self.KINDS, self.PROVIDERS)
        self.assertEqual((resolved, errors), ({'_gone': 'weak, unresolved'}, []))
        _, errors = coverage.assess({'_gone': {'a [x86_64] (weak)', 'shared cache'}}, self.KINDS, self.PROVIDERS)
        self.assertEqual(len(errors), 1)

    def test_exports_name_the_segment(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp/'s.c').write_text('int code(void) { return 1; }\nint data = 2;\nconst char *const str = "x";\n')
            lib = x86(tmp, tmp/'s.c', 'l.dylib', '-dynamiclib')
            found = coverage.exports(lib)
        self.assertEqual(found['_code'], '__TEXT')
        self.assertEqual(found['_data'], '__DATA')


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
