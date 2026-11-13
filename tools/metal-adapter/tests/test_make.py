"""metal-adapter: the bundle builder's inputs, the downgrade template, add_reexport and run.sh, without Apple binaries.
python3.14 -I -B -m unittest discover -s tools/metal-adapter/tests

Needs Xcode (clang, otool) for the add_reexport test; it skips otherwise."""
import json
import os
from pathlib import Path
import runpy
import shutil
import struct
import subprocess
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
TOOL = HERE.parent
MAKE = runpy.run_path(str(TOOL / 'make.py'))
ADD_REEXPORT = runpy.run_path(str(TOOL / 'src' / 'add_reexport.py'))


def have(tool):
    return shutil.which('xcrun') and subprocess.run(['xcrun', '--find', tool], capture_output=True).returncode == 0


class Inputs(unittest.TestCase):
    def test_every_input_is_pinned(self):
        i = json.loads((TOOL / 'inputs.json').read_text())
        files = i['x86_64_cache']['files']
        self.assertEqual(sorted(files), ['dyld_shared_cache_x86_64'] + [f'dyld_shared_cache_x86_64.0{k}' for k in range(1, 7)]
                         + ['dyld_shared_cache_x86_64.map'])
        for d in list(files.values()) + list(i['cache_dylib']['files'].values()) + [i['metal27']['cut_sha256'],
                                                                                     i['downgrade_template']['captured_sha256']]:
            self.assertRegex(d, r'^[0-9a-f]{64}$')
        self.assertRegex(i['cache_dylib']['commit'], r'^[0-9a-f]{40}$')

    def test_missing_cache_is_refused(self):
        with tempfile.TemporaryDirectory() as t, self.assertRaises(SystemExit) as e:
            MAKE['check_inputs'](t)
        self.assertIn('input missing', str(e.exception))

    def test_wrong_cache_file_is_refused(self):
        with tempfile.TemporaryDirectory() as t:
            for n in json.loads((TOOL / 'inputs.json').read_text())['x86_64_cache']['files']:
                Path(t, n).write_bytes(b'not a cache')
            with self.assertRaises(SystemExit) as e:
                MAKE['check_inputs'](t)
            self.assertIn('expected', str(e.exception))


class Template(unittest.TestCase):
    """The type-7 template the bundle gets: 13.2.1 framing, name add1, an 8-byte placeholder module."""
    def test_layout(self):
        t = MAKE['template_bytes']()
        w = struct.unpack_from('<62I', t)
        no, nl, mo, ms = w[22:26]
        self.assertEqual(w[2], 0xd8)                              # header size
        self.assertEqual(t[no:no + nl], b'add1\0')
        self.assertEqual(mo, (no + nl + 7) & ~7)                  # module right after the name, 8-aligned
        self.assertEqual((mo, ms, len(t)), (0xf0, 8, 0xf8))
        self.assertEqual(w[55], 0xeda)                            # target LLVM 3802
        # words that the stand-in shifts with the module end: none may equal anything else in the header
        ends = [k for k in range(w[2] // 4) if w[k] == mo + ms and not 22 <= k <= 25]
        self.assertEqual(ends, [26, 29, 33, 41, 52])


class AddReexport(unittest.TestCase):
    @unittest.skipUnless(have('clang') and have('otool'), 'needs Xcode')
    def test_appends_reexport(self):
        with tempfile.TemporaryDirectory() as t:
            src, lib, out = Path(t, 'a.c'), Path(t, 'liba.dylib'), Path(t, 'liba2.dylib')
            src.write_text('int a(void) { return 1; }\n')
            subprocess.run(['xcrun', 'clang', '-arch', 'x86_64', '-dynamiclib', '-Wl,-headerpad,0x200', str(src),
                            '-o', str(lib)], check=True, capture_output=True)
            if b'\x1d\x00\x00\x00' in lib.read_bytes()[:4096] and \
                    'LC_CODE_SIGNATURE' in subprocess.run(['xcrun', 'otool', '-l', str(lib)], capture_output=True, text=True).stdout:
                subprocess.run(['codesign', '--remove-signature', str(lib)], check=True)
            ADD_REEXPORT['main'](str(lib), str(out), '@loader_path/libHaoMetalShim.dylib')
            l = subprocess.run(['xcrun', 'otool', '-l', str(out)], capture_output=True, text=True, check=True).stdout
            self.assertIn('LC_REEXPORT_DYLIB', l)
            self.assertIn('@loader_path/libHaoMetalShim.dylib', l)
            # a signed input is refused
            subprocess.run(['codesign', '-f', '-s', '-', str(out)], check=True, capture_output=True)
            with self.assertRaises(SystemExit):
                ADD_REEXPORT['main'](str(out), str(Path(t, 'x')), '@loader_path/y.dylib')


class Runner(unittest.TestCase):
    @unittest.skipUnless(Path('/bin/zsh').exists(), 'needs zsh')
    def test_run_sh_parses(self):
        subprocess.run(['/bin/zsh', '-n', str(TOOL / 'run.sh')], check=True)

    def test_manifest_covers_every_built_file(self):
        self.assertTrue(set(MAKE['SIGNED']) <= set(MAKE['MANIFEST']))
        self.assertIn('fw/Metal.framework/Versions/A/Metal', MAKE['MANIFEST'])
        self.assertIn('run.sh', MAKE['MANIFEST'])


if __name__ == '__main__':
    unittest.main()
