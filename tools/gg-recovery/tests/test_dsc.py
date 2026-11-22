"""Host fixtures for tools/gg-recovery/src/dsc.py: a synthetic one-file x86_64 shared cache with three images, its
dependency closure, the page trim and the rewritten CodeDirectory. No Apple cache needed."""
import hashlib, importlib.util, os, struct, sys, tempfile, unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'src'))
import dsc  # noqa: E402

BASE = 0x7ff800000000
PAGE = 4096


def macho(segaddr, segsize, deps, install):
    cmds = struct.pack('<II16sQQQQiiII', 0x19, 72, b'__TEXT', segaddr, segsize, 0, segsize, 5, 5, 0, 0)
    cmds += struct.pack('<II16sQQQQiiII', 0x19, 72, b'__LINKEDIT', BASE + 0x100000, 0x1000, 0, 0, 1, 1, 0, 0)
    for cmd, name in [(0xd, install)] + [(0xc, d) for d in deps]:
        s = name.encode() + b'\0'
        size = (24 + len(s) + 7) // 8 * 8
        cmds += struct.pack('<IIIIII', cmd, size, 24, 2, 0x10000, 0x10000) + s.ljust(size - 24, b'\0')
    n = 2 + 1 + len(deps)
    return struct.pack('<IiiIIIII', 0xfeedfacf, 0x01000007, 3, 6, n, len(cmds), 0, 0) + cmds


def make_cache(path):
    """Header page, then images A (loads B by its short framework path), B and C, two pages each, then the signature."""
    images = [('/usr/lib/libA.dylib', ['/System/Library/Frameworks/B.framework/B']),
              ('/System/Library/Frameworks/B.framework/Versions/A/B', []),
              ('/usr/lib/libC.dylib', [])]
    body = bytearray(PAGE * 7)
    strings = 0x400
    hdr = bytearray(PAGE)
    hdr[:16] = b'dyld_v1  x86_64'.ljust(16, b'\0')
    struct.pack_into('<II', hdr, 0x10, 0x200, 1)
    struct.pack_into('<QQQII', hdr, 0x200, BASE, PAGE * 7, 0, 5, 5)
    hdr[0x58:0x68] = bytes(range(16))
    struct.pack_into('<II', hdr, 0x188, 0x300, 0)
    struct.pack_into('<II', hdr, 0x1c0, 0x300, len(images))
    for i, (p, deps) in enumerate(images):
        addr = BASE + PAGE * (1 + 2 * i)
        struct.pack_into('<QQQII', hdr, 0x300 + 32 * i, addr, 0, 0, strings, 0)
        hdr[strings:strings + len(p) + 1] = p.encode() + b'\0'
        strings += len(p) + 1
        m = macho(addr, 2 * PAGE, deps, p)
        off = PAGE * (1 + 2 * i)
        body[off:off + 2 * PAGE] = bytes([0x41 + i]) * (2 * PAGE)
        body[off:off + len(m)] = m
    body[:PAGE] = hdr
    code_limit = len(body)
    ident = b'cache\0'
    n = code_limit // PAGE
    cd_hdr = 44
    hash_off = cd_hdr + len(ident)
    cd = struct.pack('>IIIIIIIIIBBBBI', 0xfade0c02, hash_off + 32 * n, 0x20001, 2, hash_off, cd_hdr, 0, n, code_limit,
                     32, 2, 0, 12, 0) + ident
    cd += b''.join(hashlib.sha256(bytes(body[p * PAGE:(p + 1) * PAGE])).digest() for p in range(n))
    blob = struct.pack('>IIIII', 0xfade0cc0, 20 + len(cd), 1, 0, 20) + cd
    struct.pack_into('<QQ', body, 0x28, code_limit, len(blob))
    # the header page changed after its hash: rehash page 0
    blob = blob[:20 + hash_off] + hashlib.sha256(bytes(body[:PAGE])).digest() + blob[20 + hash_off + 32:]
    Path(path).write_bytes(bytes(body) + blob)


class Dsc(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, 'dyld_shared_cache_x86_64')
        make_cache(self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_reads_images_and_resolves_framework_paths(self):
        c = dsc.Cache(self.path)
        self.assertEqual([p for p, _ in c.images][1], '/System/Library/Frameworks/B.framework/Versions/A/B')
        self.assertEqual(c.resolve('/System/Library/Frameworks/B.framework/B'), BASE + 3 * PAGE)
        self.assertIsNone(c.resolve('/usr/lib/libmissing.dylib'))
        self.assertEqual(c.dependencies(BASE + PAGE), ['/System/Library/Frameworks/B.framework/B'])
        self.assertEqual(dsc.verify(c), {'dyld_shared_cache_x86_64': 0})
        c.close()

    def test_closure(self):
        c = dsc.Cache(self.path)
        kept, unresolved = dsc.closure(c, ['/usr/lib/libA.dylib', '/usr/lib/libnot.dylib'])
        self.assertEqual(sorted(kept.values()), ['/System/Library/Frameworks/B.framework/Versions/A/B', '/usr/lib/libA.dylib'])
        self.assertEqual(unresolved, {'/usr/lib/libnot.dylib'})
        c.close()

    def test_trim_zeroes_whole_pages_keeps_headers_and_rehashes(self):
        before = Path(self.path).read_bytes()
        c = dsc.Cache(self.path)
        kept, _ = dsc.closure(c, ['/usr/lib/libA.dylib'])
        stats = dsc.trim(c, set(kept))
        self.assertEqual((stats['images'], stats['kept'], stats['zeroed_bytes']), (3, 2, PAGE))
        after = Path(self.path).read_bytes()
        self.assertEqual(after[:5 * PAGE + 64], before[:5 * PAGE + 64])   # A, B and C's mach header untouched
        self.assertEqual(after[6 * PAGE:7 * PAGE], bytes(PAGE))            # C's second page zeroed
        self.assertEqual(after[5 * PAGE + 2048:6 * PAGE], before[5 * PAGE + 2048:6 * PAGE])   # partial page kept
        self.assertEqual(dsc.verify(dsc.Cache(self.path)), {'dyld_shared_cache_x86_64': 0})
        self.assertEqual(dsc.Cache(self.path).main.uuid, bytes(range(16)))

    def test_refuses_a_cms_signature(self):
        blob = struct.pack('>IIIIIII', 0xfade0cc0, 36, 2, 0, 28, 0x10000, 28) + struct.pack('>II', 0xfade0b01, 100)
        with self.assertRaises(ValueError):
            dsc.code_directory(blob)


if __name__ == '__main__':
    unittest.main()
