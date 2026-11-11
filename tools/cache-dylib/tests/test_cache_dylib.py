"""Host tests for tools/cache-dylib: export trie writer (round trip through the reader), LEB128, slide info v2 chain
walking on an authored page, the check's parsers, and the relative list-of-lists restore. With CACHE_DYLIB_TAHOE_CACHE
set to a folder holding Tahoe 25G342's dyld_shared_cache_x86_64h family, CoreWiFi is extracted, verified (check.py)
and, on Apple silicon, loaded under Rosetta: its classes register and secure coding round-trips.
Run: python3.14 -I -B -m unittest discover -s tools/cache-dylib/tests"""
import io
import json
import os
from pathlib import Path
import platform
import struct
import subprocess
import sys
import tempfile
import unittest

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
import cache_dylib  # noqa: E402
import check  # noqa: E402
import dsc  # noqa: E402

COREWIFI = '/System/Library/PrivateFrameworks/CoreWiFi.framework/Versions/A/CoreWiFi'


class Encoding(unittest.TestCase):
    def test_uleb_sleb(self):
        for value in (0, 1, 127, 128, 300, 1 << 40):
            self.assertEqual(dsc.uleb(cache_dylib.uleb(value), 0), (value, len(cache_dylib.uleb(value))))
        self.assertEqual(cache_dylib.sleb(0), b'\0')
        self.assertEqual(cache_dylib.sleb(-1), b'\x7f')
        self.assertEqual(cache_dylib.sleb(64), b'\xc0\x00')
        self.assertEqual(cache_dylib.sleb(16), b'\x10')

    def test_trie_round_trip(self):
        entries = {
            '_CWFGetOSLog': (0, 0x1234),
            '_CWFGetOSLogX': (dsc.EXPORT_SYMBOL_FLAGS_WEAK_DEFINITION, 0x2000),
            '_OBJC_CLASS_$_CWFInterface': (0, 0x2405B1B0),
            '_OBJC_METACLASS_$_CWFInterface': (0, 0x2405B188),
            '_memcpy_alias': (dsc.EXPORT_SYMBOL_FLAGS_REEXPORT, (3, '_memcpy')),
            '_same_name': (dsc.EXPORT_SYMBOL_FLAGS_REEXPORT, (1, '_same_name')),
            '_resolved': (dsc.EXPORT_SYMBOL_FLAGS_STUB_AND_RESOLVER, (0x40, 0x80)),
            '_': (0, 7),
        }
        self.assertEqual(dsc.parse_trie(cache_dylib.build_trie(entries)), entries)

    def test_empty_trie(self):
        self.assertEqual(dsc.parse_trie(cache_dylib.build_trie({})), {})


def fake_cache(page, address=0x7FF840000000, mask=0x00FFFF0000000000, add=0x7FF800000000):
    """A Cache over one 4 KiB data mapping with slide info v2 (pointer chain starting at offset 0)."""
    cache = dsc.Cache.__new__(dsc.Cache)
    starts = struct.pack('<H', 0)
    info = struct.pack('<IIIIIIQQ', 2, 0x1000, 40, 1, 42, 0, mask, add) + starts
    cache.files = {'fake': io.BytesIO(page)}
    cache.mappings = [(address, address + len(page), 'fake', 0, info, 3, 3)]
    cache._starts = [address]
    return cache


class Slide(unittest.TestCase):
    def test_chain(self):
        base = 0x7FF840000000
        mask = 0x00FFFF0000000000
        shift = 40 - 2           # deltas count 4-byte units
        page = bytearray(0x1000)
        # pointers at 0x0 -> 0x10 -> 0x28 (end); values are unslid targets
        add = 0x7FF800000000          # the cache base; stored values are offsets from it (x86_64 caches)
        struct.pack_into('<Q', page, 0x00, (0x10 >> 2) << (shift + 2) | (0x7FF81F8EE000 - add))
        struct.pack_into('<Q', page, 0x10, (0x18 >> 2) << (shift + 2) | 0)
        struct.pack_into('<Q', page, 0x28, 0x7FF843032CC0 - add)
        cache = fake_cache(bytes(page), base, mask)
        found = cache.rebases(base, base + 0x1000)
        self.assertEqual(found, {base: 0x7FF81F8EE000, base + 0x10: 0, base + 0x28: 0x7FF843032CC0})
        self.assertEqual(cache.rebases(base + 8, base + 0x20), {base + 0x10: 0})
        self.assertEqual(cache.pointer(base), 0x7FF81F8EE000)


class Lists(unittest.TestCase):
    def test_relative_list_of_lists_keeps_own_list(self):
        base = 0x7FF840000000
        page = bytearray(0x1000)
        lists = base + 0x100
        struct.pack_into('<II', page, 0x100, 8, 2)
        own, foreign = base + 0x800, base + 0x40000000
        for i, (target, index) in enumerate(((foreign, 1935), (own, 230))):
            entry = lists + 8 + i*8
            struct.pack_into('<q', page, 0x108 + i*8, ((target - entry) << 16) | index)
        struct.pack_into('<Q', page, 0x20, lists + 1 - 0x7FF800000000)
        cache = fake_cache(bytes(page), base, 0x00FFFF0000000000)
        job = cache_dylib.Extraction.__new__(cache_dylib.Extraction)
        job.cache, job.fixups, job.problems, job.stats = cache, {}, [], {}
        job.ranges = [(base + 0x800, base + 0x900)]
        job.own_list(base + 0x20)
        self.assertEqual(job.problems, [])
        self.assertEqual(job.fixups[base + 0x20], ('rebase', own))
        self.assertEqual(job.stats['preattached_foreign_lists_dropped'], 1)


class CheckParsers(unittest.TestCase):
    def test_operand_and_fixup_lines(self):
        line = '7ff81f8efd27:  \tmovq\t0x2071a5ea(%rip), %rax       ## 0x7ff840009318'
        m = check.OPERAND.match(line)
        self.assertEqual((m[2], m[3]), ('movq', '0x7ff840009318'))
        fix = check.FIXUP.match('        __CACHE_GOT     __got            0x7FF8400460E8           bind  libMobileGestalt/_MGCopyAnswer [weak-import]')
        self.assertEqual((fix[1], fix[2]), ('0x7FF8400460E8', 'bind'))

    def test_ivy_bridge_guard(self):
        for insn, newer in (('tzcntq\t%rax, %rcx', True), ('lzcntl\t%eax, %ecx', True), ('shlxq\t%rax, %rbx, %rcx', True),
                            ('vfmadd231sd\t%xmm0, %xmm1, %xmm2', True), ('vpbroadcastd\t%xmm0, %ymm1', True),
                            ('movbel\t(%rdi), %eax', True), ('vaddpd\t%ymm0, %ymm1, %ymm2', False),
                            ('roundsd\t$0x9, %xmm0, %xmm0', False), ('bsfq\t%rax, %rcx', False), ('andl\t%eax, %ecx', False)):
            self.assertEqual(bool(check.HASWELL.match('7ff81f8efd27:  \t' + insn)), newer, insn)


TAHOE_CACHE = os.environ.get('CACHE_DYLIB_TAHOE_CACHE')


@unittest.skipUnless(TAHOE_CACHE, 'set CACHE_DYLIB_TAHOE_CACHE to a Tahoe dyld_shared_cache_x86_64h folder')
class TahoeCoreWiFi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix='cache-dylib-test-'))
        cls.dylib = cls.tmp/'CoreWiFiTahoe.dylib'
        cls.record = cache_dylib.extract(TAHOE_CACHE, 'x86_64h', COREWIFI, cls.dylib,
                                         COREWIFI.replace('/CoreWiFi', '/CoreWiFiTahoe.dylib'))
        subprocess.run(['codesign', '-f', '-s', '-', cls.dylib], check=True, capture_output=True)

    def test_verified(self):
        record = check.check(self.dylib, self.record)
        self.assertEqual(record['errors'], [])
        self.assertGreater(record['got_slots_used'], 500)
        self.assertGreater(record['swift_relative_pointers'], 1000)
        stats = self.record['stats']
        self.assertEqual(stats['classes'], 123)
        self.assertGreater(stats['preattached_lists_restored'], 0)

    def test_header(self):
        out = subprocess.run(['otool', '-hv', self.dylib], capture_output=True, text=True).stdout
        self.assertIn('DYLIB', out)
        self.assertNotIn('DYLIB_IN_CACHE', out)
        ident = subprocess.run(['otool', '-D', self.dylib], capture_output=True, text=True).stdout.split()[-1]
        self.assertTrue(ident.endswith('/CoreWiFiTahoe.dylib'))

    @unittest.skipUnless(platform.machine() == 'arm64', 'loads the x86_64 dylib under Rosetta')
    def test_loads_under_rosetta(self):
        (self.tmp/'probe.m').write_text(r'''
#import <Foundation/Foundation.h>
#import <objc/runtime.h>
#include <dlfcn.h>
int main(int argc, char **argv) { @autoreleasepool {
    if (!dlopen(argv[1], RTLD_NOW)) { printf("dlopen: %s\n", dlerror()); return 2; }
    Class profile = objc_getClass("CWFNetworkProfile");
    Dl_info info; dladdr((__bridge void *)profile, &info);
    id p = [[profile alloc] init];
    [p setValue:[@"Skynet" dataUsingEncoding:NSUTF8StringEncoding] forKey:@"SSID"];
    NSData *d = [NSKeyedArchiver archivedDataWithRootObject:p requiringSecureCoding:YES error:nil];
    id q = [NSKeyedUnarchiver unarchivedObjectOfClass:profile fromData:d error:nil];
    printf("image=%s roundtrip=%d scan=%s\n", strrchr(info.dli_fname, '/') + 1,
           [[q valueForKey:@"SSID"] isEqual:[p valueForKey:@"SSID"]],
           [[[[objc_getClass("CWFScanParameters") alloc] init] description] hasPrefix:@"channels="] ? "ok" : "bad");
    return 0;
}}
''')
        probe = self.tmp/'probe'
        subprocess.run(['xcrun', 'clang', '-arch', 'x86_64', '-fobjc-arc', '-framework', 'Foundation',
                        self.tmp/'probe.m', '-o', probe], check=True)
        subprocess.run(['codesign', '-f', '-s', '-', probe], check=True, capture_output=True)
        out = subprocess.run([probe, self.dylib], capture_output=True, text=True, timeout=120)
        self.assertEqual(out.stdout.strip(), 'image=CoreWiFiTahoe.dylib roundtrip=1 scan=ok', out.stderr[-2000:])


if __name__ == '__main__':
    unittest.main()
