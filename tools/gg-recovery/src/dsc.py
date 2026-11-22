"""dsc: trim an x86_64 dyld shared cache (all subcaches) to the images a BaseSystem uses, keeping its layout.

Ported from the archive's rec-graphics dsc-trim.py/dsclib.py (commit 13fe7399), which cut Tahoe's cache to the 602
images MTLCompilerService loads. Same files, same addresses, same cache UUID (hao's interface units are keyed to it):
every image not kept has its segments zeroed by whole 4 KiB pages, except __LINKEDIT (shared by all images) and its
mach header + load commands (dyld and objc walk every header); then every changed page's hash is rewritten in that
file's ad hoc CodeDirectory. Zeroed pages cost nothing once the dmg is compressed.

Layout from dyld's include/mach-o/dyld_cache_format.h (header offsets used: mappingOffset 0x10, codeSignatureOffset
0x28, uuid 0x58, subCacheArrayOffset 0x188, imagesOffset 0x1c0). The keep set is a closure over the images' dylib
load commands (closure()), starting from roots the caller chooses (keep_roots in guest_a0.py).

    dsc.py images CACHE            image paths, one per line
    dsc.py closure CACHE ROOTS     the closure of ROOTS (one path per line) as kept image paths
    dsc.py verify DIR              every page of every cache file in DIR against its CodeDirectory
"""
import hashlib, os, re, struct, sys

PAGE = 4096
DYLIB_CMDS = {0xc: 'load', 0x80000018: 'weak', 0x8000001f: 'reexport', 0x80000023: 'upward', 0x20: 'lazy'}
LC_SEGMENT_64 = 0x19


def u32(b, o):
    return struct.unpack_from('<I', b, o)[0]


class CacheFile:
    """One file of a split cache: header fields, mappings (vm address -> file offset) and code signature span."""

    def __init__(self, path):
        self.path = path
        self.size = os.path.getsize(path)
        with open(path, 'rb') as f:
            h = self.hdr = f.read(0x1000)
        if h[:7] != b'dyld_v1':
            raise ValueError('%s: not a dyld cache' % path)
        self.magic = h[:16].rstrip(b'\0').decode()
        mo, mc = u32(h, 0x10), u32(h, 0x14)
        self.cs_off, self.cs_size = struct.unpack_from('<QQ', h, 0x28)
        self.uuid = h[0x58:0x68]
        with open(path, 'rb') as f:
            f.seek(mo)
            m = f.read(32 * mc)
        self.mappings = [struct.unpack_from('<QQQII', m, 32 * i) for i in range(mc)]   # address size fileOffset max init

    def off(self, addr):
        for a, s, fo, _, _ in self.mappings:
            if a <= addr < a + s:
                return fo + (addr - a)
        return None

    def mapping_end(self, addr):
        return next(a + s for a, s, _, _, _ in self.mappings if a <= addr < a + s)


class Cache:
    """Main cache file + its subcaches (suffixes from the main header's dyld_subcache_entry array)."""

    def __init__(self, path):
        self.main = CacheFile(path)
        h = self.main.hdr
        sub_off, sub_count = u32(h, 0x188), u32(h, 0x18c)
        self.files = [self.main]
        with open(path, 'rb') as f:
            f.seek(sub_off)
            arr = f.read(56 * sub_count)
            for i in range(sub_count):
                suffix = arr[56 * i + 24:56 * (i + 1)].split(b'\0')[0].decode()
                self.files.append(CacheFile(path + suffix))
            img_off, img_count = u32(h, 0x1c0), u32(h, 0x1c4)
            f.seek(img_off)
            arr = f.read(32 * img_count)
            self.images = []                       # (path, mach header address); aliases share an address
            for i in range(img_count):
                a, _, _, po, _ = struct.unpack_from('<QQQII', arr, 32 * i)
                f.seek(po)
                b = f.read(1024)
                self.images.append((b[:b.index(b'\0')].decode(), a))
        self.by_path = {p: a for p, a in self.images}
        self._handles = {}

    def locate(self, addr):
        for cf in self.files:
            o = cf.off(addr)
            if o is not None:
                return cf, o
        return None, None

    def read(self, addr, n):
        cf, o = self.locate(addr)
        if cf is None:
            raise ValueError('%#x not mapped' % addr)
        f = self._handles.get(cf.path) or self._handles.setdefault(cf.path, open(cf.path, 'rb'))
        f.seek(o)
        return f.read(n)

    def close(self):
        for f in self._handles.values():
            f.close()
        self._handles.clear()

    def commands(self, mh):
        """[(cmd, bytes)] of the image whose mach header is at mh."""
        b = self.read(mh, 32)
        magic, _, _, _, ncmds, sizeofcmds = struct.unpack_from('<IiiIII', b, 0)
        if magic != 0xfeedfacf:
            raise ValueError('%#x: not a 64-bit mach header' % mh)
        return load_commands(self.read(mh + 32, sizeofcmds), ncmds)

    def header_end(self, mh):
        return mh + 32 + u32(self.read(mh, 32), 20)

    def segments(self, mh):
        """[(segname, vmaddr, vmsize)] of the image at mh."""
        return [(c[8:24].split(b'\0')[0].decode(),) + struct.unpack_from('<QQ', c, 24)
                for cmd, c in self.commands(mh) if cmd == LC_SEGMENT_64]

    def dependencies(self, mh):
        return dylib_names(self.commands(mh))

    def resolve(self, name):
        """Mach header address of install name NAME, or None. Framework paths match with or without Versions/X."""
        if name in self.by_path:
            return self.by_path[name]
        m = re.fullmatch(r'(.*\.framework)/(?:Versions/[^/]+/)?([^/]+)', name)
        if m:
            for v in ('', 'Versions/A/', 'Versions/B/', 'Versions/C/', 'Versions/Current/'):
                p = '%s/%s%s' % (m.group(1), v, m.group(2))
                if p in self.by_path:
                    return self.by_path[p]
        return None


def load_commands(lc, ncmds):
    out, o = [], 0
    for _ in range(ncmds):
        cmd, size = struct.unpack_from('<II', lc, o)
        if size < 8:
            break
        out.append((cmd, lc[o:o + size]))
        o += size
    return out


def dylib_names(commands):
    names = []
    for cmd, c in commands:
        if cmd in DYLIB_CMDS:
            off = u32(c, 8)
            names.append(c[off:].split(b'\0')[0].decode(errors='replace'))
    return names


def macho_dependencies(slice_bytes):
    """Install names a thin 64-bit Mach-O loads."""
    magic, _, _, _, ncmds, sizeofcmds = struct.unpack_from('<IiiIII', slice_bytes, 0)
    if magic != 0xfeedfacf:
        return []
    return dylib_names(load_commands(slice_bytes[32:32 + sizeofcmds], ncmds))


def closure(cache, roots):
    """(kept {address: path}, unresolved {name}) for the install names ROOTS and everything they load."""
    first = {}
    for p, a in cache.images:
        first.setdefault(a, p)
    kept, unresolved, todo = {}, set(), list(roots)
    while todo:
        name = todo.pop()
        a = cache.resolve(name)
        if a is None:
            unresolved.add(name)
            continue
        if a in kept:
            continue
        kept[a] = first[a]
        todo += cache.dependencies(a)
    return kept, unresolved


def zero_ranges(cache, keep):
    """{file path: [(offset, length)]}: whole pages of every segment but __LINKEDIT of the images not in KEEP
    (addresses), the mach header and load commands left. Stats in the second value."""
    zero = {cf.path: [] for cf in cache.files}
    stats = dict(images=len({a for _, a in cache.images}), kept=0, kept_bytes=0, zeroed_bytes=0)
    for a_mh in sorted({a for _, a in cache.images}):
        if a_mh in keep:
            stats['kept'] += 1
        head_end = cache.header_end(a_mh)
        for name, a, s in cache.segments(a_mh):
            if name == '__LINKEDIT' or s == 0:
                continue
            if a_mh in keep:
                stats['kept_bytes'] += s
                continue
            if a <= a_mh < a + s:                  # keep the header + load commands
                s -= head_end - a
                a = head_end
            while s > 0:                           # exact per mapping
                cf, o = cache.locate(a)
                if cf is None:
                    raise ValueError('segment %s at %#x: not mapped' % (name, a))
                n = min(s, cf.mapping_end(a) - a)
                # whole pages only: slide info is a fixup chain per 4 KiB page, and a page shared with a kept image
                # keeps its chain start (the archive's first trim zeroed partial pages: libobjc read never-fixed-up
                # pointers and every compiler service crashed in map_images)
                zs, ze = -(-o // PAGE) * PAGE, (o + n) // PAGE * PAGE
                if ze > zs:
                    zero[cf.path].append((zs, ze - zs))
                    stats['zeroed_bytes'] += ze - zs
                a += n
                s -= n
    return zero, stats


def code_directory(blob):
    """Offset of the single CodeDirectory of an ad hoc signature blob, its fields checked."""
    magic, _, count = struct.unpack_from('>III', blob, 0)
    if magic != 0xfade0cc0:
        raise ValueError('not an embedded signature')
    cds = []
    for i in range(count):
        _, off = struct.unpack_from('>II', blob, 12 + 8 * i)
        m = struct.unpack_from('>I', blob, off)[0]
        if m == 0xfade0c02:
            cds.append(off)
        elif m == 0xfade0b01 and struct.unpack_from('>I', blob, off + 4)[0] != 8:
            raise ValueError('CMS signature present: not ad hoc')
    if len(cds) != 1:
        raise ValueError('%d CodeDirectories' % len(cds))
    return cds[0]


def cd_fields(blob, off):
    _, flags, hash_off, _, _, n_code, code_limit, hash_size, hash_type, _, page_log = \
        struct.unpack_from('>IIIIIIIBBBB', blob, off + 8)
    return dict(flags=flags, hash_off=hash_off, n_code=n_code, code_limit=code_limit, hash_size=hash_size,
                hash_type=hash_type, page=1 << page_log)


def apply_zero(cf, ranges):
    """Zero RANGES of cache file CF in place and rewrite those pages' SHA-256 hashes; returns pages rehashed."""
    z = bytes(1 << 20)
    pages = set()
    with open(cf.path, 'r+b') as f:
        f.seek(cf.cs_off)
        blob = f.read(cf.cs_size)
        cd = code_directory(blob)
        d = cd_fields(blob, cd)
        if not (d['flags'] & 0x2 and d['hash_type'] == 2 and d['hash_size'] == 32 and d['page'] == PAGE):
            raise ValueError('%s: not an ad hoc SHA-256 4 KiB signature: %s' % (cf.path, d))
        if d['code_limit'] != cf.cs_off or d['n_code'] != (d['code_limit'] + PAGE - 1) // PAGE:
            raise ValueError('%s: the signature does not cover the file up to itself' % cf.path)
        for o, n in ranges:
            if o + n > cf.cs_off:
                raise ValueError('zero range %#x+%#x runs into the code signature' % (o, n))
            f.seek(o)
            left = n
            while left:
                k = min(left, len(z))
                f.write(z[:k])
                left -= k
            pages.update(range(o // PAGE, (o + n - 1) // PAGE + 1))
        zh = hashlib.sha256(bytes(PAGE)).digest()
        for p in sorted(pages):
            f.seek(p * PAGE)
            page = f.read(min(PAGE, d['code_limit'] - p * PAGE))
            h = zh if len(page) == PAGE and not any(page) else hashlib.sha256(page).digest()
            f.seek(cf.cs_off + cd + d['hash_off'] + 32 * p)
            f.write(h)
    return len(pages)


def trim(cache, keep):
    """Zero every image of CACHE not in KEEP (addresses), in place. Returns stats."""
    zero, stats = zero_ranges(cache, keep)
    cache.close()
    stats['files'] = {}
    for cf in cache.files:
        stats['files'][os.path.basename(cf.path)] = dict(ranges=len(zero[cf.path]), pages=apply_zero(cf, zero[cf.path]))
    return stats


def verify(cache):
    """{file name: bad pages}: every page of every file checked against its CodeDirectory (as the kernel does)."""
    out = {}
    for cf in cache.files:
        with open(cf.path, 'rb') as f:
            f.seek(cf.cs_off)
            blob = f.read(cf.cs_size)
            cd = code_directory(blob)
            d = cd_fields(blob, cd)
            f.seek(0)
            bad = 0
            for p in range(d['n_code']):
                page = f.read(min(PAGE, d['code_limit'] - p * PAGE))
                want = blob[cd + d['hash_off'] + 32 * p:cd + d['hash_off'] + 32 * p + 32]
                bad += hashlib.sha256(page).digest() != want
        out[os.path.basename(cf.path)] = bad
    return out


def main():
    a = sys.argv[1:]
    if len(a) == 2 and a[0] == 'images':
        print('\n'.join(p for p, _ in Cache(a[1]).images))
    elif len(a) == 3 and a[0] == 'closure':
        c = Cache(a[1])
        roots = [l.strip() for l in open(a[2]) if l.strip() and not l.startswith('#')]
        kept, unresolved = closure(c, roots)
        print('\n'.join(sorted(kept.values())))
        print('%d kept, %d unresolved' % (len(kept), len(unresolved)), file=sys.stderr)
    elif len(a) == 2 and a[0] == 'verify':
        names = sorted(n for n in os.listdir(a[1]) if re.fullmatch(r'dyld_shared_cache_x86_64', n))
        bad = verify(Cache(os.path.join(a[1], names[0])))
        print('\n'.join('%s: %d bad pages' % kv for kv in bad.items()))
        return 1 if any(bad.values()) else 0
    else:
        sys.exit(__doc__)
    return 0


if __name__ == '__main__':
    sys.exit(main())
