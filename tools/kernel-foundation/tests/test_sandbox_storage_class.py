"""Host tests for Sandbox call 0x108 (src/sandbox_storage_class.h) and its binding in commpage_build.py.

Run: python3.14 -I -m unittest discover -s tools/kernel-foundation/tests
The header is compiled into a host library (tests/sandbox_storage_class_harness.c) and called with ctypes. The
expected results are 27.0's own Sandbox (_syscall_rootless_verify_storage_class,
_extract_storage_class_name_from_xattr, kernelcache.release.vma2 of 26A428). The donor binding test runs when
the tahoe_sandbox_25G330 and its rename-layout inputs resolve, else it skips.
"""
import ctypes
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
LIB = None
TEMP = None


def setUpModule():
    global LIB, TEMP
    TEMP = tempfile.TemporaryDirectory()
    path = Path(TEMP.name)/'storage_class.dylib'
    subprocess.run(['xcrun', 'clang', '-std=c99', '-Wall', '-Wextra', '-Werror', '-O2', '-dynamiclib', '-o', str(path),
                    str(HERE/'sandbox_storage_class_harness.c')], check=True)
    LIB = ctypes.CDLL(str(path))
    LIB.harness_name.restype = ctypes.c_long
    LIB.harness_name.argtypes = [ctypes.c_char_p, ctypes.c_size_t]
    LIB.harness_verdict.argtypes = [ctypes.c_char_p, ctypes.c_size_t, ctypes.c_char_p]
    LIB.harness_fd.argtypes = [ctypes.c_ulonglong, ctypes.POINTER(ctypes.c_int)]


def tearDownModule():
    TEMP.cleanup()


def name(value):
    return LIB.harness_name(value+b'\0', len(value))


def verdict(value, requested):
    return LIB.harness_verdict(value+b'\0', len(value), requested)


class StorageClassName(unittest.TestCase):
    def test_plain_value_is_the_name(self):
        self.assertEqual(name(b'com.apple.rootless.storage.dz'), 0)

    def test_text_after_first_slash(self):
        self.assertEqual(name(b'flags/com.apple.x/y'), 6)

    def test_empty_value_or_empty_name_is_system(self):
        self.assertEqual(name(b''), -2)
        self.assertEqual(name(b'prefix/'), -2)

    def test_colon_anywhere_has_no_name(self):
        self.assertEqual(name(b'a:b'), -1)
        self.assertEqual(name(b'x/a:b'), -1)


class Verdict(unittest.TestCase):
    def test_match_is_success(self):
        # Tahoe's 0x105 writes the class without its NUL; 27.0's libsystem then asks 0x108 for the same class.
        self.assertEqual(verdict(b'com.apple.rootless.storage.dz', b'com.apple.rootless.storage.dz'), 0)

    def test_mismatch_is_einval(self):
        self.assertEqual(verdict(b'com.apple.rootless.storage.dz', b'com.apple.rootless.storage.other'), 22)
        self.assertEqual(verdict(b'a:b', b'a:b'), 22)

    def test_empty_request_matches_system(self):
        # libsystem passes "" for a NULL class; 0x105 then writes an empty value, read back as "system".
        self.assertEqual(verdict(b'', b''), 0)
        self.assertEqual(verdict(b'system', b''), 0)
        self.assertEqual(verdict(b'', b'system'), 0)
        self.assertEqual(verdict(b'other', b''), 22)
        self.assertEqual(verdict(b'', b'other'), 22)


class FileDescriptor(unittest.TestCase):
    def fd(self, raw):
        out = ctypes.c_int(-7)
        return LIB.harness_fd(raw, ctypes.byref(out)), out.value

    def test_sign_extended_ints_pass(self):
        self.assertEqual(self.fd(5), (0, 5))
        self.assertEqual(self.fd(0xffffffffffffffff), (0, -1))

    def test_other_values_are_ebadf(self):
        self.assertEqual(self.fd(0x100000005)[0], 9)
        self.assertEqual(self.fd(0xffffffff)[0], 9)


def resolve(row):
    if row == 'sandbox_rename_layout_26A428':
        return Path(os.environ.get('DARWIN27_SANDBOX_RENAME_LAYOUT', REPO/'tools/kernel-fixes/profiles/26A428/sandbox-rename-layout.json'))
    value = os.environ.get('DARWIN27_TAHOE_SANDBOX')
    return Path(value) if value else None


class DonorBinding(unittest.TestCase):
    """commpage_build.policy_syscall_binding finds the donor's own handler in its own ops slot."""

    def test_donor_slot_and_entry(self):
        kext, layout = resolve('tahoe_sandbox_25G330'), resolve('sandbox_rename_layout_26A428')
        if kext is None or layout is None or not kext.exists() or not layout.exists():
            self.skipTest('tahoe_sandbox_25G330 or sandbox_rename_layout_26A428 does not resolve')
        build = runpy.run_path(str(HERE.parent/'commpage_build.py'))
        data, rename = kext.read_bytes(), json.loads(layout.read_text())
        delta, slot, prefix = build['policy_syscall_binding'](data, rename['ops_offset'], rename['completion_offset'],
                                                              rename['callback_symbol'])
        self.assertEqual(delta, build['symbols'](data)['_hook_policy_syscall'])
        # mpo_policy_syscall is entry 117 of mac_policy_ops (XNU 26.5); the 27.0 kernel's __mac_syscall calls
        # the matched policy's ops at +0x3a8.
        self.assertEqual(slot, 117*8)
        # Entry: ENOTSUP (45) default; the high range subtracts 0x103 and accepts 0..4 (0x103-0x107 only).
        self.assertEqual(prefix[:9], bytes.fromhex('554889e5b82d000000'))
        self.assertIn(bytes.fromhex('81c6fdfeffff83fe04'), data[delta:delta+0x40])

    def test_ambiguous_table_is_refused(self):
        build = runpy.run_path(str(HERE.parent/'commpage_build.py'))
        kext, layout = resolve('tahoe_sandbox_25G330'), resolve('sandbox_rename_layout_26A428')
        if kext is None or layout is None or not kext.exists():
            self.skipTest('donor inputs do not resolve')
        rename = json.loads(layout.read_text())
        with self.assertRaisesRegex(ValueError, 'policy ops table'):
            build['policy_syscall_binding'](kext.read_bytes(), rename['ops_offset'], rename['completion_offset']+8,
                                              rename['callback_symbol'])


if __name__ == '__main__':
    unittest.main()
