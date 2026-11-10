import hashlib
import importlib.util
import json
import os
from pathlib import Path
import plistlib
import shutil
import struct
import subprocess
import tempfile
import unittest

HERE = Path(__file__).resolve().parents[1]
TOP = HERE.parents[1]

def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result

mapping = load('mapping', HERE/'src/map.py')
macho = load('macho', HERE/'src/macho.py')

class MapTests(unittest.TestCase):
    def test_helper_is_not_bundle_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base = root/'App.app/Contents'
            (base/'MacOS').mkdir(parents=True)
            (base/'Info.plist').write_bytes(plistlib.dumps({'CFBundleExecutable': 'Main'}))
            source = struct.pack('<8I', 0xfeedfacf, 0x100000c, 2, 2, 0, 0, 0, 0)
            files = []
            for name in ('Main', 'Helper'):
                path = base/'MacOS'/name
                path.write_bytes(source)
                files.append({'path': str(path.relative_to(root)),
                              'source_sha256': hashlib.sha256(source).hexdigest(),
                              'result_sha256': 'ab'*32})
            rows = mapping.rows({'files': files}, root)
            self.assertEqual([r[0] for r in rows], ['/App.app/Contents/MacOS/Main', '/App.app', '/App.app/Contents/MacOS/Helper'])
            mapping.header(rows)
            files[0]['source_sha256'] = '00'*32
            with self.assertRaisesRegex(ValueError, 'source mismatch'):
                mapping.rows({'files': files}, root)

    def test_invalid_map_rejected(self):
        with self.assertRaises(ValueError): mapping.header([])
        entry = ('/a', '/a', 'arm64e', bytes(32))
        with self.assertRaises(ValueError): mapping.header([entry, entry])
        with self.assertRaises(ValueError): mapping.architecture(b'not macho')

class SignatureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Read-only signature tests: none of these Apple executables is launched.
        fixture = os.environ.get('IDENTITY_FIXTURE')
        if not fixture:
            raise unittest.SkipTest('set IDENTITY_FIXTURE to an embedded Find My executable')
        cls.tmp = tempfile.TemporaryDirectory(prefix='gg-identity-test-')
        cls.root = Path(cls.tmp.name).resolve()
        cls.path = cls.root/'guest'
        cls.original = Path(fixture).read_bytes()
        cls.path.write_bytes(cls.original)
        digest = hashlib.sha256(cls.original).digest()
        (cls.root/'identity_map.h').write_text(mapping.header([
            (str(cls.path), str(cls.path), mapping.architecture(cls.original), digest)]))
        cls.lib = cls.root/'identity.dylib'
        flags = ['clang', '-arch', 'x86_64', '-O2', '-Wall', '-Wextra', '-Werror',
                 '-Wno-deprecated-declarations', '-framework', 'CoreFoundation', '-framework', 'Security']
        subprocess.run(flags + ['-dynamiclib', '-Wl,-not_for_dyld_shared_cache', '-I', str(cls.root),
            '-install_name', str(cls.lib), str(HERE/'src/identity.c'), '-o', str(cls.lib)], check=True)
        subprocess.run(['codesign', '-f', '-s', '-', str(cls.lib)], check=True, capture_output=True)
        for name, extra in [('plain', []), ('mapped', ['-Wl,-needed_library,'+str(cls.lib)])]:
            binary = cls.root/name
            subprocess.run(flags + [str(HERE/'tests/probe.c'), *extra, '-o', str(binary)], check=True)
            subprocess.run(['codesign', '-f', '-s', '-', str(binary)], check=True, capture_output=True)

    @classmethod
    def tearDownClass(cls): cls.tmp.cleanup()

    def tearDown(self): self.path.write_bytes(self.original)

    def verdict(self, name, path=None):
        return subprocess.run([str(self.root/name), str(path or self.path)],
                              capture_output=True, text=True, timeout=30)

    def test_real_interposer_restores_original_anchor(self):
        self.assertEqual(self.verdict('plain').stdout.strip(), '-67050')
        result = self.verdict('mapped')
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(result.stdout.strip(), '0')

    def test_unlisted_copy_keeps_normal_identity(self):
        copy = self.root/'unlisted'
        copy.write_bytes(self.original)
        self.assertEqual(self.verdict('mapped', copy).stdout.strip(), '-67050')

    def test_changed_native_slice_not_authenticated_by_original_arm(self):
        data = bytearray(self.original)
        start, size = next(v for (cpu, _), v in macho.slice_ranges(data).items() if cpu == 0x1000007)
        data[start + min(4096, size-1)] ^= 1
        self.path.write_bytes(data)
        self.assertNotEqual(self.verdict('mapped').returncode, 0)

    def test_changed_arm_slice_not_authenticated(self):
        data = bytearray(self.original)
        start, size = next(v for (cpu, _), v in macho.slice_ranges(data).items() if cpu == 0x100000c)
        data[start + min(4096, size-1)] ^= 1
        self.path.write_bytes(data)
        self.assertNotEqual(self.verdict('mapped').returncode, 0)

    def test_missing_file_is_not_success(self):
        self.path.unlink()
        self.assertNotEqual(self.verdict('mapped').returncode, 0)

if __name__ == '__main__': unittest.main()
