"""metal-adapter: the installed builds of the shim and the stand-in (make.py installed; patches/golden-gate-metal27).
python3.14 -I -B -m unittest discover -s tools/metal-adapter/tests

On the installed system every process that loads Metal loads the shim, and every Metal compile goes through the
stand-in, WindowServer included. So the installed builds must print nothing unless asked, must never end a process
the shim does not recognize, and the stand-in must carry its own template.

Without Apple binaries (Xcode needed): the installed stand-in built natively (silent, template built in and equal to
make.py's), and the installed shim built against an empty layout table and loaded into an x86_64 process under
Rosetta whose Metal is the host's own (not Metal27): the installed build refuses quietly and the process goes on;
the test-bundle build ends it with 70, as before.

With METAL_ADAPTER_INSTALLED=OUT (a `make.py installed OUT` result; needs Rosetta): the build record matches the
files; a probe that loads OUT's Metal27 (DYLD_FRAMEWORK_PATH) gets the in-process compiler switch with nothing on
stderr, none with HAO_METAL27_INTERNAL=0, and the shim's log with HAO_SHIM_LOG=1. With METAL_ADAPTER_BUNDLE too,
OUT's Metal is the bundle's file byte for byte."""
import json
import os
from pathlib import Path
import platform
import runpy
import shutil
import subprocess
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
TOOL = HERE.parent
MAKE = runpy.run_path(str(TOOL / 'make.py'))
CC = ['xcrun', 'clang', '-arch', 'x86_64', '-mmacosx-version-min=27.0', '-O1', '-Wall']


def have_clang():
    return shutil.which('xcrun') and subprocess.run(['xcrun', '--find', 'clang'], capture_output=True).returncode == 0


def rosetta():
    return platform.machine() == 'x86_64' or subprocess.run(['/usr/bin/arch', '-x86_64', '/usr/bin/true'],
                                                             capture_output=True).returncode == 0


def sign(*files):
    for f in files:
        subprocess.run(['codesign', '-f', '-s', '-', str(f)], check=True, capture_output=True)


PROBE = r'''
#include <stdio.h>
#include <stdlib.h>
#include <dlfcn.h>
int main(void) {
  void *m = dlopen("/System/Library/Frameworks/Metal.framework/Versions/A/Metal", RTLD_NOW);
  const char *e = getenv("MTL_MONOLITHIC_COMPILER");
  printf("{\"metal\": %s, \"monolithic\": %s%s%s}\n", m ? "true" : "false", e ? "\"" : "", e ? e : "null", e ? "\"" : "");
  return 0;
}
'''


@unittest.skipUnless(have_clang(), 'needs Xcode')
class InstalledStandin(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        MAKE['template_inc'](cls.tmp / 'template.inc')
        cls.h = cls.tmp / 'harness-installed'
        subprocess.run(['xcrun', 'clang', '-O1', '-Wall', '-Wno-unused-function', '-DHAO_STANDIN_INSTALLED',
                        f'-DHAO_TEMPLATE_INC="{cls.tmp / "template.inc"}"', str(HERE / 'standin_harness.c'),
                        '-o', str(cls.h)], check=True)
        sign(cls.h)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def run_h(self, *args, env=None):
        out = self.tmp / 'out'
        if out.exists():
            out.unlink()
        r = subprocess.run([str(self.h), *map(str, args), str(out)], capture_output=True, text=True,
                           env={**os.environ, **(env or {})})
        return r.returncode, out.read_bytes() if out.exists() else None, r.stderr

    def test_template_is_built_in(self):
        rc, t, err = self.run_h('template', self.tmp / 'template.inc')
        self.assertEqual(rc, 0, err)
        self.assertEqual(t, MAKE['template_bytes']())

    def test_silent_unless_asked(self):
        o = b'-std=metal4.1 -fmetal-math-mode=fast\0'
        req = self.tmp / 'r.req13'
        req.write_bytes(len(b'x').to_bytes(8, 'little') + len(o).to_bytes(8, 'little') + b'x' + bytes(7) + o)
        rc, s, err = self.run_h('req13', req, env={'HAO_STANDIN_LOG': ''})
        self.assertEqual((rc, err), (0, ''))
        self.assertIn(b'-std=metal3.0', s)
        rc, s, err = self.run_h('req13', req, env={'HAO_STANDIN_LOG': '1'})
        self.assertEqual(rc, 0)
        self.assertIn("[standin] #0 option -std=metal4.1 -> '-std=metal3.0'", err)


@unittest.skipUnless(have_clang() and rosetta(), 'needs Xcode and Rosetta')
class InstalledShimForeignMetal(unittest.TestCase):
    """The shim in a process whose Metal is not Metal27 (the host's own): installed build quiet, test build exit 70."""
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        (cls.tmp / 'layouts.inc').write_text('')     # no layouts: nothing of Apple's needed to build
        for name, flags in (('installed', ['-DHAO_METAL27_INSTALLED']), ('bundle', [])):
            subprocess.run(CC + ['-Wno-gnu-empty-initializer', '-Wno-zero-length-array', '-dynamiclib',
                                 '-install_name', f'@loader_path/shim-{name}.dylib',
                                 '-DMETAL27_UUID="00000000-0000-0000-0000-000000000000"', *flags,
                                 f'-I{cls.tmp}', f'-I{TOOL / "src"}', str(TOOL / 'src' / 'shim.c'),
                                 '-o', str(cls.tmp / f'shim-{name}.dylib'), '-lobjc', '-framework', 'CoreFoundation'],
                           check=True, capture_output=True)
        (cls.tmp / 'probe.c').write_text(PROBE)
        subprocess.run(CC + [str(cls.tmp / 'probe.c'), '-o', str(cls.tmp / 'probe')], check=True)
        sign(cls.tmp / 'shim-installed.dylib', cls.tmp / 'shim-bundle.dylib', cls.tmp / 'probe')

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def probe(self, name, **env):
        return subprocess.run(['/usr/bin/env', f'DYLD_INSERT_LIBRARIES={self.tmp / f"shim-{name}.dylib"}',
                               *[f'{k}={v}' for k, v in env.items()], str(self.tmp / 'probe')],
                              capture_output=True, text=True, timeout=120)

    def test_installed_build_refuses_quietly(self):
        r = self.probe('installed')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stderr, '')
        self.assertEqual(json.loads(r.stdout), {'metal': True, 'monolithic': None})

    def test_installed_build_says_why_when_asked(self):
        r = self.probe('installed', HAO_SHIM_LOG='1')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("[shim] REFUSE: the loaded Metal is not the bundle's Metal27", r.stderr)

    def test_bundle_build_still_ends_the_process(self):
        r = self.probe('bundle')
        self.assertEqual(r.returncode, 70)
        self.assertIn('[shim] REFUSE', r.stderr)


@unittest.skipUnless(os.environ.get('METAL_ADAPTER_INSTALLED'), 'set METAL_ADAPTER_INSTALLED to a make.py installed OUT')
class InstalledPayload(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.out = Path(os.environ['METAL_ADAPTER_INSTALLED']).resolve()
        cls.root = cls.out / 'root'
        cls.record = json.loads((cls.out / 'build.json').read_text())
        cls.tmp = Path(tempfile.mkdtemp())
        fw = cls.tmp / 'fw/Metal.framework/Versions/A'
        fw.mkdir(parents=True)
        for n in ('Metal', 'libHaoMetalShim.dylib'):
            shutil.copyfile(cls.root / 'System/Library/Frameworks/Metal.framework/Versions/A' / n, fw / n)
        (cls.tmp / 'probe.c').write_text(PROBE)
        subprocess.run(CC + [str(cls.tmp / 'probe.c'), '-o', str(cls.tmp / 'probe')], check=True)
        sign(cls.tmp / 'probe')

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def probe(self, **env):
        return subprocess.run(['/usr/bin/env', f'DYLD_FRAMEWORK_PATH={self.tmp / "fw"}',
                               *[f'{k}={v}' for k, v in env.items()], str(self.tmp / 'probe')],
                              capture_output=True, text=True, timeout=120)

    def test_record_matches_files(self):
        self.assertEqual(sorted(self.record['files']), sorted(MAKE['INSTALLED']))
        for p, digest in self.record['files'].items():
            self.assertEqual(MAKE['sha'](self.root / p), digest, p)
        self.assertEqual(self.record['inputs']['metal27_cut_sha256'], MAKE['INPUTS']['metal27']['cut_sha256'])
        found = sorted(str(p.relative_to(self.root)) for p in self.root.rglob('*') if p.is_file())
        self.assertEqual(found, sorted(MAKE['INSTALLED']))

    @unittest.skipUnless(os.environ.get('METAL_ADAPTER_BUNDLE'), 'set METAL_ADAPTER_BUNDLE too')
    def test_metal_is_the_bundles(self):
        b = Path(os.environ['METAL_ADAPTER_BUNDLE']) / 'fw/Metal.framework/Versions/A/Metal'
        self.assertEqual(MAKE['sha'](b), self.record['files']['System/Library/Frameworks/Metal.framework/Versions/A/Metal'])

    @unittest.skipUnless(rosetta(), 'needs Rosetta')
    def test_in_process_compiler_by_default_and_silent(self):
        r = self.probe()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stderr, '')
        self.assertEqual(json.loads(r.stdout), {'metal': True, 'monolithic': '1'})

    @unittest.skipUnless(rosetta(), 'needs Rosetta')
    def test_switch_off(self):
        r = self.probe(HAO_METAL27_INTERNAL='0')
        self.assertEqual((r.returncode, r.stderr), (0, ''))
        self.assertEqual(json.loads(r.stdout)['monolithic'], None)

    @unittest.skipUnless(rosetta(), 'needs Rosetta')
    def test_log_when_asked(self):
        r = self.probe(HAO_SHIM_LOG='1')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('[shim] isInternalBuild forced to true; MTL_MONOLITHIC_COMPILER=1', r.stderr)
        self.assertIn('[shim] layouts: -[MTLRenderPipelineDescriptorInternal _descriptorPrivate]', r.stderr)

    def test_standins_carry_the_template(self):
        t = MAKE['template_bytes']()
        for v in (32023, 32024):
            data = (self.root / f'System/Library/PrivateFrameworks/MTLCompiler.framework/Versions/{v}/MTLCompiler').read_bytes()
            self.assertIn(t, data)


if __name__ == '__main__':
    unittest.main()
