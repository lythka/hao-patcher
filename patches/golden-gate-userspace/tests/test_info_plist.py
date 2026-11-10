import importlib.util
import plistlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[1]/'src'
sys.path.insert(0, str(SRC))
spec = importlib.util.spec_from_file_location('gg_install', SRC/'install.py')
install = importlib.util.module_from_spec(spec)
spec.loader.exec_module(install)


def program(folder, name, services, archs=('x86_64',)):
    """A small x86_64 program with an embedded Info.plist, as WindowServer has."""
    info = folder/(name+'.plist')
    info.write_bytes(plistlib.dumps({'BSServiceDomains': {'BKSHIDServices': {'Services': {s: {} for s in services}}},
                                     'CFBundleIdentifier': 'com.apple.WindowServer'}))
    (folder/'main.c').write_text('int main(void) { return 0; }\n')
    out = folder/name
    subprocess.run(['clang', *[a for arch in archs for a in ('-arch', arch)], '-o', out, folder/'main.c', '-Wl,-sectcreate,__TEXT,__info_plist,'+str(info)],
                   check=True, capture_output=True)
    return out


@unittest.skipUnless(shutil.which('clang'), 'needs the Xcode command-line tools')
class InfoPlist(unittest.TestCase):
    def test_golden_plist_replaces_kept_one(self):
        with tempfile.TemporaryDirectory() as t:
            t = Path(t)
            # The kept program's section is padded with whitespace from a longer key list; the golden one is minified.
            kept = program(t, 'kept', ['OldServiceWithALongNameThatTakesRoom%d' % i for i in range(6)])
            golden = program(t, 'golden', ['BKTouchStream', 'BKTouchEvents'])
            install.replace_info_plist(kept, golden)
            info = plistlib.loads(install.embedded_info_plist(kept))
            self.assertEqual(sorted(info['BSServiceDomains']['BKSHIDServices']['Services']), ['BKTouchEvents', 'BKTouchStream'])

    def test_universal_program_every_slice(self):
        with tempfile.TemporaryDirectory() as t:
            t = Path(t)
            # Tahoe's launcher is universal: the bytes are read into a bytearray and every slice is rewritten.
            kept = program(t, 'kept', ['OldServiceWithALongNameThatTakesRoom%d' % i for i in range(6)], ('x86_64', 'arm64'))
            golden = program(t, 'golden', ['BKTouchStream'])
            install.replace_info_plist(kept, golden)
            sections = install.section_ranges(kept.read_bytes(), b'__TEXT', b'__info_plist')
            self.assertEqual(len(sections), 2)
            data = kept.read_bytes()
            for offset, size in sections.values():
                self.assertIn(b'BKTouchStream', data[offset:offset+size])

    def test_load_dylib_added_once_to_x86_64_slice(self):
        with tempfile.TemporaryDirectory() as t:
            t = Path(t)
            kept = program(t, 'kept', ['A'], ('x86_64', 'arm64'))
            data = bytearray(kept.read_bytes())
            self.assertTrue(install.add_load_dylib(data, b'/usr/lib/dss_shim.dylib', install.CPU_X86_64))
            self.assertFalse(install.add_load_dylib(data, b'/usr/lib/dss_shim.dylib', install.CPU_X86_64))
            kept.write_bytes(data)
            for arch, expect in (('x86_64', True), ('arm64', False)):
                listing = subprocess.check_output(['otool', '-arch', arch, '-L', kept], text=True)
                self.assertEqual('/usr/lib/dss_shim.dylib' in listing, expect, arch)

    def test_plist_that_does_not_fit_stops(self):
        with tempfile.TemporaryDirectory() as t:
            t = Path(t)
            kept = program(t, 'kept', ['A'])
            golden = program(t, 'golden', ['BKTouchStream%d' % i for i in range(20)])
            with self.assertRaises(SystemExit):
                install.replace_info_plist(kept, golden)


if __name__ == '__main__':
    unittest.main()
