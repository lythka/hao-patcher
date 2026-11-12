"""patch.json "files" declares what src/install.py installs, and the row's two files are the bytes tools/live-text
produces from OCLP's service. The hook installs prebuilt files whole (it edits nothing on the System), so both are
origin entries, not edit specs: the load command (LC_LOAD_DYLIB, timestamp 0) and the ad-hoc re-signature are made
by tools/live-text/lib/compiler_service.py when the row is built, and the service is a thin x86_64 file.
python3.14 -I -B -m unittest discover -s patches/metal-compiler-compat/tests

Always: the declared paths and rows match install.py and patch.json. With real inputs it also runs
compiler_service.build on OCLP's service and compares the bytes with the row:
  METAL_COMPILER_COMPAT_ROW      the row's compiler-service/ folder (build.json and replace/)
  METAL_COMPILER_COMPAT_SOURCE   folder with OCLP's MTLCompilerService, Info.plist and CodeResources
  METAL_COMPILER_COMPAT_ADAPTER  the compiler-adapters/ folder holding compat.dylib and build.json"""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import struct
import tempfile
import unittest

PATCH = Path(__file__).resolve().parents[1]
TOP = PATCH.parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


install = load('compat_install', PATCH/'src/install.py')
PATCH_JSON = json.loads((PATCH/'patch.json').read_text())
FILES = {f['path']: f for f in PATCH_JSON['files']}


def sha(data):
    return hashlib.sha256(data).hexdigest()


class Declaration(unittest.TestCase):
    def test_files_are_the_two_paths_install_py_writes(self):
        self.assertEqual(set(FILES), {install.SERVICE, install.LIBRARY})

    def test_every_file_is_a_whole_file_from_the_patch_row(self):
        for f in FILES.values():
            self.assertNotIn('edits', f)
            self.assertEqual(f['origin']['kind'], 'hao')
            self.assertIn(f['origin']['row'], PATCH_JSON['inputs'])
            self.assertTrue(f['origin'].get('note'))

    def test_hook_installs_only_through_install_py(self):
        hook = (PATCH/'src/kc-hook.sh').read_text()
        self.assertIn('install.py', hook)
        self.assertNotIn('codesign', hook)


@unittest.skipUnless(os.environ.get('METAL_COMPILER_COMPAT_ROW'), 'set METAL_COMPILER_COMPAT_ROW')
class RealRow(unittest.TestCase):
    def setUp(self):
        self.row = Path(os.environ['METAL_COMPILER_COMPAT_ROW'])
        self.build = json.loads((self.row/'build.json').read_text())

    def test_row_holds_exactly_the_declared_files(self):
        self.assertEqual(set(self.build['files']), set(FILES))
        for name, expected in self.build['files'].items():
            self.assertEqual(sha((self.row/'replace'/name).read_bytes()), expected)

    def test_service_has_the_appended_load_command(self):
        data = (self.row/'replace'/install.SERVICE).read_bytes()
        self.assertEqual(struct.unpack_from('<I', data, 0)[0], 0xfeedfacf, 'thin 64-bit Mach-O, no fat header')
        self.assertEqual(struct.unpack_from('<I', data, 4)[0], 0x01000007, 'x86_64')
        at, found = 32, []
        for _ in range(struct.unpack_from('<I', data, 16)[0]):
            cmd, size = struct.unpack_from('<II', data, at)
            if cmd == 0xc:
                offset, timestamp, current, compat = struct.unpack_from('<IIII', data, at + 8)
                found.append((data[at + offset:at + size].split(b'\0')[0].decode(), timestamp, current, compat))
            at += size
        self.assertEqual(found[-1], ('/' + install.LIBRARY, 0, 0, 0))

    @unittest.skipUnless(os.environ.get('METAL_COMPILER_COMPAT_SOURCE') and os.environ.get('METAL_COMPILER_COMPAT_ADAPTER'),
                         'set METAL_COMPILER_COMPAT_SOURCE and METAL_COMPILER_COMPAT_ADAPTER')
    def test_same_bytes_as_compiler_service_build(self):
        producer = load('compiler_service', TOP/'tools/live-text/lib/compiler_service.py')
        source = Path(os.environ['METAL_COMPILER_COMPAT_SOURCE'])
        self.assertEqual(sha((source/'MTLCompilerService').read_bytes()), self.build['source_service_sha256'])
        with tempfile.TemporaryDirectory() as tmp:
            record = producer.build(source, os.environ['METAL_COMPILER_COMPAT_ADAPTER'], Path(tmp)/'out')
            for name in FILES:
                self.assertEqual((Path(tmp)/'out/replace'/name).read_bytes(), (self.row/'replace'/name).read_bytes(), name)
            self.assertEqual(record['files'], self.build['files'])


if __name__ == '__main__':
    unittest.main()
