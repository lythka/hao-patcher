"""dsc.py: read an x86_64 dyld shared cache family (main cache and subcaches) the way cache_dylib.py needs it.

Only what extraction uses: mappings and their slide info (version 2, the x86_64 format), the image list, each image's
load commands, sections and export trie, and the cache's Objective-C optimization header. Every subcache's UUID is
checked against the main cache's subcache table, so the main file's hash pins the whole family.
"""
from pathlib import Path
import bisect
import struct

MH_MAGIC_64 = 0xFEEDFACF
LC_REQ_DYLD = 0x80000000
LC_SEGMENT_64 = 0x19
LC_SYMTAB = 0x2
LC_DYSYMTAB = 0xB
LC_ID_DYLIB = 0xD
LC_LOAD_DYLIB = 0xC
LC_LOAD_WEAK_DYLIB = 0x18 | LC_REQ_DYLD
LC_REEXPORT_DYLIB = 0x1F | LC_REQ_DYLD
LC_LOAD_UPWARD_DYLIB = 0x23 | LC_REQ_DYLD
LC_UUID = 0x1B
LC_DYLD_INFO = 0x22
LC_DYLD_INFO_ONLY = 0x22 | LC_REQ_DYLD
LC_FUNCTION_STARTS = 0x26
LC_DATA_IN_CODE = 0x29
LC_SOURCE_VERSION = 0x2A
LC_BUILD_VERSION = 0x32
LC_DYLD_EXPORTS_TRIE = 0x33 | LC_REQ_DYLD
LC_DYLD_CHAINED_FIXUPS = 0x34 | LC_REQ_DYLD
LC_CODE_SIGNATURE = 0x1D
LC_SEGMENT_SPLIT_INFO = 0x1E
DYLIB_COMMANDS = (LC_LOAD_DYLIB, LC_LOAD_WEAK_DYLIB, LC_REEXPORT_DYLIB, LC_LOAD_UPWARD_DYLIB)

EXPORT_SYMBOL_FLAGS_KIND_MASK = 0x03
EXPORT_SYMBOL_FLAGS_WEAK_DEFINITION = 0x04
EXPORT_SYMBOL_FLAGS_REEXPORT = 0x08
EXPORT_SYMBOL_FLAGS_STUB_AND_RESOLVER = 0x10

PAGE_ATTR_EXTRA = 0x8000
PAGE_ATTR_NO_REBASE = 0x4000
PAGE_ATTR_END = 0x8000


def uleb(data, at):
    value = shift = 0
    while True:
        byte = data[at]
        at += 1
        value |= (byte & 0x7F) << shift
        shift += 7
        if byte < 0x80:
            return value, at


def cstr(data, at):
    end = data.index(b'\0', at)
    return data[at:end].decode('utf-8', 'surrogateescape'), end + 1


class Section:
    __slots__ = ('name', 'segment', 'addr', 'size', 'offset', 'align', 'reloff', 'nreloc', 'flags', 'reserved1',
                 'reserved2', 'reserved3')

    def __init__(self, raw):
        name, segment = raw[:16].rstrip(b'\0').decode(), raw[16:32].rstrip(b'\0').decode()
        (self.addr, self.size, self.offset, self.align, self.reloff, self.nreloc, self.flags, self.reserved1,
         self.reserved2, self.reserved3) = struct.unpack_from('<QQIIIIIIII', raw, 32)
        self.name, self.segment = name, segment

    @property
    def end(self):
        return self.addr + self.size

    def pack(self):
        return (self.name.encode().ljust(16, b'\0') + self.segment.encode().ljust(16, b'\0') +
                struct.pack('<QQIIIIIIII', self.addr, self.size, self.offset, self.align, self.reloff, self.nreloc,
                            self.flags, self.reserved1, self.reserved2, self.reserved3))


class Segment:
    __slots__ = ('name', 'vmaddr', 'vmsize', 'fileoff', 'filesize', 'maxprot', 'initprot', 'flags', 'sections', 'data')

    def __init__(self, raw):
        self.name = raw[8:24].rstrip(b'\0').decode()
        (self.vmaddr, self.vmsize, self.fileoff, self.filesize, self.maxprot, self.initprot, nsects,
         self.flags) = struct.unpack_from('<QQQQiiII', raw, 24)
        self.sections = [Section(raw[72 + i*80:152 + i*80]) for i in range(nsects)]

    @property
    def end(self):
        return self.vmaddr + self.vmsize

    def pack(self):
        body = struct.pack('<QQQQiiII', self.vmaddr, self.vmsize, self.fileoff, self.filesize, self.maxprot,
                           self.initprot, len(self.sections), self.flags)
        return (struct.pack('<II', LC_SEGMENT_64, 72 + 80*len(self.sections)) + self.name.encode().ljust(16, b'\0')
                + body + b''.join(s.pack() for s in self.sections))


class Image:
    """One cache image: its load commands, segments, sections, dependencies and exports."""

    def __init__(self, cache, address, path):
        self.cache, self.address, self.path = cache, address, path
        magic, cputype, subtype, filetype, ncmds, sizeofcmds, flags, _ = struct.unpack(
            '<IiiIIIII', cache.read(address, 32))
        if magic != MH_MAGIC_64:
            raise ValueError(f'{path}: not a 64-bit Mach-O at {address:#x}')
        self.cputype, self.subtype, self.filetype, self.flags = cputype, subtype, filetype, flags
        self.sizeofcmds = sizeofcmds
        raw = cache.read(address + 32, sizeofcmds)
        self.commands, self.segments, self.dylibs = [], [], []
        at = 0
        for _ in range(ncmds):
            cmd, size = struct.unpack_from('<II', raw, at)
            body = raw[at:at + size]
            self.commands.append((cmd, body))
            if cmd == LC_SEGMENT_64:
                self.segments.append(Segment(body))
            elif cmd in DYLIB_COMMANDS:
                name_off, = struct.unpack_from('<I', body, 8)
                self.dylibs.append((cmd, body[name_off:].split(b'\0', 1)[0].decode()))
            at += size
        self._exports = None

    def command(self, kind):
        return next((body for cmd, body in self.commands if cmd == kind), None)

    def section(self, name, segment=None):
        return next((s for seg in self.segments for s in seg.sections
                     if s.name == name and (segment is None or s.segment == segment)), None)

    def sections(self):
        return [s for seg in self.segments for s in seg.sections]

    def linkedit(self, offset, size):
        """SIZE bytes at linkedit file OFFSET, located through the image's __LINKEDIT segment."""
        seg = next(s for s in self.segments if s.name == '__LINKEDIT')
        return self.cache.read(seg.vmaddr + offset - seg.fileoff, size)

    def contains(self, address):
        return any(seg.vmaddr <= address < seg.end for seg in self.segments if seg.name != '__LINKEDIT')

    def exports(self):
        """{name: (flags, value)}: value is the image-relative address, (ordinal, import name) for a re-export, or
        (stub, resolver) offsets."""
        if self._exports is None:
            body = self.command(LC_DYLD_EXPORTS_TRIE)
            if body is not None:
                off, size = struct.unpack_from('<II', body, 8)
            else:
                body = self.command(LC_DYLD_INFO_ONLY) or self.command(LC_DYLD_INFO)
                off, size = struct.unpack_from('<II', body, 40) if body else (0, 0)
            self._exports = parse_trie(self.linkedit(off, size)) if size else {}
        return self._exports


def parse_trie(data):
    found, stack = {}, [(0, '')]
    while stack:
        node, prefix = stack.pop()
        size, at = uleb(data, node)
        if size:
            flags, here = uleb(data, at)
            if flags & EXPORT_SYMBOL_FLAGS_REEXPORT:
                ordinal, here = uleb(data, here)
                name, here = cstr(data, here)
                found[prefix] = (flags, (ordinal, name or prefix))
            elif flags & EXPORT_SYMBOL_FLAGS_STUB_AND_RESOLVER:
                stub, here = uleb(data, here)
                resolver, here = uleb(data, here)
                found[prefix] = (flags, (stub, resolver))
            else:
                value, here = uleb(data, here)
                found[prefix] = (flags, value)
        at += size
        children = data[at]
        at += 1
        for _ in range(children):
            label, at = cstr(data, at)
            child, at = uleb(data, at)
            stack.append((child, prefix + label))
    return found


class Cache:
    """The mappings of one cache family, read through open files, with slide info v2 and the image list."""

    def __init__(self, directory, arch):
        stem = f'dyld_shared_cache_{arch}'
        directory = Path(directory)
        main = directory/stem
        if not main.is_file():
            raise FileNotFoundError(f'{directory}: no {stem}')
        self.arch, self.directory, self.files, self.mappings = arch, directory, {}, []
        head = self._open(main)
        self.uuid = head[0x58:0x68]
        mapping_offset, = struct.unpack_from('<I', head, 0x10)
        sub_offset, sub_count = struct.unpack_from('<II', head, 0x188)
        entry = 56 if mapping_offset > 0x1C8 else 24
        self.subcaches = []
        table = self._file(main.name, sub_offset, sub_count*entry)
        for i in range(sub_count):
            raw = table[i*entry:(i+1)*entry]
            suffix = raw[24:56].split(b'\0', 1)[0].decode() if entry == 56 else f'.{i + 1:02d}'
            sub = directory/(stem + suffix)
            sub_head = self._open(sub)
            if sub_head[0x58:0x68] != raw[:16]:
                raise ValueError(f'{sub}: UUID does not match the main cache\'s subcache table')
            self.subcaches.append(sub.name)
        self.mappings.sort()
        self._starts = [m[0] for m in self.mappings]
        objc_offset, objc_size = struct.unpack_from('<QQ', head, 0x1D0)
        self.base, = struct.unpack_from('<Q', head, 0xE0)
        objc = self.read(self.base + objc_offset, objc_size)
        self.objc_version, = struct.unpack_from('<I', objc, 0)
        # version 2 (macOS 26): relativeMethodSelectorBaseAddressOffset is the sixth offset; extraction needs it
        self.selector_base = self.base + struct.unpack_from('<Q', objc, 48)[0] if self.objc_version == 2 else None
        images_offset, images_count = struct.unpack_from('<II', head, 0x1C0)
        table = self._file(main.name, images_offset, images_count*32)
        self.image_list = []
        for i in range(images_count):
            address, _, _, name = struct.unpack_from('<QQQI', table, i*32)
            self.image_list.append((address, self._file(main.name, name, 1024).split(b'\0', 1)[0].decode()))
        self._images, self._ranges = {}, None

    def _open(self, path):
        stream = path.open('rb')
        self.files[path.name] = stream
        head = stream.read(0x400)
        if not head.startswith(b'dyld_v1'):
            raise ValueError(f'{path}: not a dyld shared cache')
        offset, count = struct.unpack_from('<II', head, 0x138)
        stream.seek(offset)
        table = stream.read(count*56)
        for i in range(count):
            address, size, file_offset, slide_offset, slide_size, flags, maxprot, initprot = \
                struct.unpack_from('<QQQQQQII', table, i*56)
            slide = None
            if slide_size:
                stream.seek(slide_offset)
                info = stream.read(slide_size)
                version, = struct.unpack_from('<I', info)
                if version != 2:
                    raise ValueError(f'{path}: slide info v{version}; only v2 (x86_64) is supported')
                slide = info
            self.mappings.append((address, address + size, path.name, file_offset, slide, maxprot, initprot))
        return head

    def _file(self, name, offset, size):
        stream = self.files[name]
        stream.seek(offset)
        return stream.read(size)

    def locate(self, address):
        i = bisect.bisect_right(self._starts, address) - 1
        if i >= 0 and address < self.mappings[i][1]:
            return self.mappings[i]
        return None

    def mapped(self, address, size=1):
        m = self.locate(address)
        return m is not None and address + size <= m[1]

    def read(self, address, size):
        m = self.locate(address)
        if m is None or address + size > m[1]:
            raise LookupError(f'{address:#x}+{size:#x} is not mapped')
        return self._file(m[2], m[3] + address - m[0], size)

    def u32(self, address):
        return struct.unpack('<I', self.read(address, 4))[0]

    def u64(self, address):
        return struct.unpack('<Q', self.read(address, 8))[0]

    def cstring(self, address, limit=1 << 16):
        m = self.locate(address)
        if m is None:
            raise LookupError(f'{address:#x} is not mapped')
        data = self.read(address, min(limit, m[1] - address))
        end = data.find(b'\0')
        if end < 0:
            raise ValueError(f'{address:#x}: no NUL-terminated string')
        return data[:end]

    def decode(self, address, raw):
        """The unslid value of the raw slide-info-v2 pointer RAW stored at ADDRESS."""
        m = self.locate(address)
        if m is None or m[4] is None:
            return raw
        mask, add = struct.unpack_from('<QQ', m[4], 24)
        value = raw & ~mask & 0xFFFFFFFFFFFFFFFF
        return value + add if value else 0

    def pointer(self, address):
        return self.decode(address, self.u64(address))

    def rebases(self, start, end):
        """{address: unslid target} for every pointer slide info v2 rebases in [START, END)."""
        found = {}
        m = self.locate(start)
        if m is None or m[4] is None:
            return found
        info = m[4]
        _, page_size, starts_off, starts_count, extras_off, _, mask, add = struct.unpack_from('<IIIIIIQQ', info)
        shift = (mask & -mask).bit_length() - 1 - 2
        first, last = (start - m[0]) // page_size, (min(end, m[1]) - 1 - m[0]) // page_size
        for page in range(first, min(last + 1, starts_count)):
            entry, = struct.unpack_from('<H', info, starts_off + page*2)
            if entry == PAGE_ATTR_NO_REBASE:
                continue
            chains = []
            if entry & PAGE_ATTR_EXTRA:
                index = entry & 0x3FFF
                while True:
                    extra, = struct.unpack_from('<H', info, extras_off + index*2)
                    chains.append((extra & 0x3FFF) * 4)
                    if extra & PAGE_ATTR_END:
                        break
                    index += 1
            else:
                chains.append(entry * 4)
            page_address = m[0] + page*page_size
            data = self._file(m[2], m[3] + page*page_size, page_size)
            for offset in chains:
                delta = 1
                while delta:
                    raw, = struct.unpack_from('<Q', data, offset)
                    delta = (raw & mask) >> shift
                    value = raw & ~mask & 0xFFFFFFFFFFFFFFFF
                    where = page_address + offset
                    if start <= where < end:
                        found[where] = value + add if value else 0
                    offset += delta
        return found

    def image(self, path_or_address):
        if isinstance(path_or_address, int):
            path = next(p for a, p in self.image_list if a == path_or_address)
        else:
            path = path_or_address
        if path not in self._images:
            address = next((a for a, p in self.image_list if p == path), None)
            if address is None:
                raise LookupError(f'{path}: not in the cache')
            self._images[path] = Image(self, address, path)
        return self._images[path]

    def image_at(self, address):
        """The image whose (non-LINKEDIT) segment holds ADDRESS, or None."""
        if self._ranges is None:
            ranges = []
            for _, path in self.image_list:
                for seg in self.image(path).segments:
                    if seg.name != '__LINKEDIT':
                        ranges.append((seg.vmaddr, seg.end, path))
            ranges.sort()
            self._ranges = ranges
            self._range_starts = [r[0] for r in ranges]
        i = bisect.bisect_right(self._range_starts, address) - 1
        if i >= 0 and address < self._ranges[i][1]:
            return self.image(self._ranges[i][2])
        return None

    def close(self):
        for stream in self.files.values():
            stream.close()
