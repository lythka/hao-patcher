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
        with self.assertRaises(ValueError): mapping.header([entry], ['relative/observer'])
        with self.assertRaises(ValueError): mapping.architecture(b'not macho')

    def test_dynamic_observers_terminated(self):
        entry = ('/a', '/a', 'arm64e', bytes(32))
        self.assertIn('dynamic_observers[] = {NULL};', mapping.header([entry]))
        self.assertIn('dynamic_observers[] = {"/usr/libexec/airportd", NULL};',
                      mapping.header([entry], ['/usr/libexec/airportd']))

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

APPLE_DONOR = Path('/usr/bin/true')

class DynamicTests(unittest.TestCase):
    """A running guest named by audit token, as airportd's CWFIsCodesignedByApple checks Wi-Fi clients.

    The guest is a fat file of an ad hoc native test program and the host's Apple-signed arm64e /usr/bin/true
    slice, the shape hao-runtime gives translated programs. Only the generated test programs run (x86_64)."""
    @classmethod
    def setUpClass(cls):
        if not APPLE_DONOR.is_file() or 'arm64e' not in subprocess.run(
                ['lipo', '-archs', str(APPLE_DONOR)], capture_output=True, text=True).stdout.split():
            raise unittest.SkipTest('needs an Apple-signed arm64e slice at /usr/bin/true')
        cls.tmp = tempfile.TemporaryDirectory(prefix='gg-identity-dynamic-')
        cls.root = root = Path(cls.tmp.name).resolve()
        flags = ['clang', '-arch', 'x86_64', '-O2', '-Wall', '-Wextra', '-Werror',
                 '-Wno-deprecated-declarations', '-framework', 'CoreFoundation', '-framework', 'Security']
        def sign(path):
            subprocess.run(['codesign', '-f', '-s', '-', str(path)], check=True, capture_output=True)
        arm = root/'apple-arm64e'
        subprocess.run(['lipo', str(APPLE_DONOR), '-thin', 'arm64e', '-output', str(arm)], check=True)
        for variant in ('a', 'b'):
            native = root/f'guest-{variant}-x86_64'
            subprocess.run(['clang', '-arch', 'x86_64', '-O2', f'-DVARIANT="{variant}"',
                            str(HERE/'tests/guest.c'), '-o', str(native)], check=True)
            sign(native)
            subprocess.run(['lipo', '-create', str(native), str(arm), '-output', str(root/f'guest-{variant}')],
                           check=True)
        cls.guest = root/'guest'
        cls.original = (root/'guest-a').read_bytes()
        cls.guest.write_bytes(cls.original); cls.guest.chmod(0o755)
        cls.lib = root/'identity.dylib'
        (root/'identity_map.h').write_text(mapping.header(
            [(str(cls.guest), str(cls.guest), 'arm64e', hashlib.sha256(cls.original).digest())],
            [str(root/'observer')]))
        subprocess.run(flags + ['-dynamiclib', '-Wl,-not_for_dyld_shared_cache', '-I', str(root),
            '-install_name', str(cls.lib), str(HERE/'src/identity.c'), '-o', str(cls.lib)], check=True)
        sign(cls.lib)
        for name, extra in [('plain', []), ('observer', ['-Wl,-needed_library,'+str(cls.lib)])]:
            subprocess.run(flags + [str(HERE/'tests/dynamic_probe.c'), *extra, '-o', str(root/name)], check=True)
            sign(root/name)
        # The same observer binary at a path the map does not name.
        shutil.copy2(root/'observer', root/'unnamed-observer')

    @classmethod
    def tearDownClass(cls): cls.tmp.cleanup()

    def tearDown(self):
        self.guest.write_bytes(self.original); self.guest.chmod(0o755)

    def verdict(self, probe, guest=None, requirement='anchor apple'):
        result = subprocess.run([str(self.root/probe), str(guest or self.guest), requirement],
                                capture_output=True, text=True, timeout=60)
        return result.stdout.strip()

    def test_observer_accepts_running_mapped_guest(self):
        self.assertEqual(self.verdict('plain'), '-67050')
        self.assertEqual(self.verdict('observer'), '0')

    def test_requirement_unchanged(self):
        self.assertEqual(self.verdict('observer', requirement='anchor apple and identifier "com.apple.true"'), '0')
        self.assertEqual(self.verdict('observer', requirement='anchor apple and identifier "com.example.other"'),
                         '-67050')

    def test_only_named_observers_substitute(self):
        self.assertEqual(self.verdict('unnamed-observer'), '-67050')

    def test_unlisted_copy_keeps_normal_identity(self):
        copy = self.root/'unlisted'
        shutil.copy2(self.guest, copy)
        self.assertEqual(self.verdict('observer', copy), '-67050')

    def test_other_native_code_beside_original_slice_rejected(self):
        # A validly signed but different native program with the same Apple slice at the mapped path.
        self.guest.write_bytes((self.root/'guest-b').read_bytes())
        self.assertEqual(self.verdict('observer'), '-67050')

if __name__ == '__main__': unittest.main()
