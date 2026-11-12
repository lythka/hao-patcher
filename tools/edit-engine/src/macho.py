"""x86_64 Mach-O reading and load-command insertion for the edit engine.

Thin images and universal files (the x86_64 slice) are supported. Offsets returned here are offsets in the whole
file. Bounds checks follow tools/live-text/lib/macho_edit.py; the dylib insertion generalises it and
patches/golden-gate-hello/src/add_weak_dylib.py (weak or not, any timestamp), which it is meant to replace.
"""
import struct

X86_64 = 0x1000007
MH_MAGIC_64 = 0xfeedfacf
LC_SEGMENT_64, LC_SYMTAB, LC_ID_DYLIB = 0x19, 0x2, 0xd
LC_LOAD_DYLIB, LC_LOAD_WEAK_DYLIB = 0xc, 0x80000018
DEPENDENCY_COMMANDS = {0xc, 0x80000018, 0x8000001f, 0x80000023, 0x20}
ZEROFILL = {0x1, 0xc, 0x12}


class MachOError(ValueError):
    pass


def x86_slice(data):
    """(start, length) of the x86_64 image in DATA, thin or universal."""
    if len(data) < 32:
        raise MachOError('truncated Mach-O')
    if data[:4] == b'\xcf\xfa\xed\xfe':
        if struct.unpack_from('<I', data, 4)[0] != X86_64:
            raise MachOError('expected an x86_64 Mach-O')
        return 0, len(data)
    magic, count = struct.unpack_from('>II', data)
    if magic not in (0xcafebabe, 0xcafebabf):
        raise MachOError('not a Mach-O or universal file')
    wide = magic == 0xcafebabf
    stride = 32 if wide else 20
    table_end = 8 + count * stride
    if not count or count > 64 or table_end > len(data):
        raise MachOError('invalid universal slice table')
    regions, found = [], []
    for i in range(count):
        at = 8 + i * stride
        cpu = struct.unpack_from('>I', data, at)[0]
        start, length = struct.unpack_from('>QQ' if wide else '>II', data, at + 8)
        if start < table_end or length < 32 or start + length > len(data):
            raise MachOError('invalid universal slice range')
        if any(start < b and a < start + length for a, b in regions):
            raise MachOError('overlapping universal slices')
        regions.append((start, start + length))
        if cpu == X86_64:
            found.append((start, length))
    if len(found) != 1:
        raise MachOError('expected exactly one x86_64 slice')
    return found[0]


class Image:
    """The x86_64 image in a file: load commands, segments, sections and defined symbols."""

    def __init__(self, data):
        self.data = data
        self.start, self.length = x86_slice(data)
        s = self.start
        magic, cpu, _, self.filetype, ncmds, sizeofcmds, _, _ = struct.unpack_from('<8I', data, s)
        if magic != MH_MAGIC_64 or cpu != X86_64:
            raise MachOError('expected a 64-bit x86_64 Mach-O slice')
        self.header_end = 32 + sizeofcmds
        if self.header_end > self.length or ncmds > sizeofcmds // 8:
            raise MachOError('invalid load command bounds')
        self.commands, at = [], 32
        for _ in range(ncmds):
            if at + 8 > self.header_end:
                raise MachOError('truncated load command')
            cmd, size = struct.unpack_from('<II', data, s + at)
            if size < 8 or size % 8 or at + size > self.header_end:
                raise MachOError('invalid load command')
            self.commands.append((cmd, at, size))
            at += size
        if at != self.header_end:
            raise MachOError('load command size mismatch')
        self.segments, self.sections = [], []
        for cmd, at, size in self.commands:
            if cmd != LC_SEGMENT_64:
                continue
            if size < 72:
                raise MachOError('truncated segment command')
            name = data[s + at + 8:s + at + 24].rstrip(b'\0').decode('ascii', 'replace')
            vmaddr, vmsize, fileoff, filesize = struct.unpack_from('<4Q', data, s + at + 24)
            if fileoff + filesize > self.length:
                raise MachOError('segment %s extends past the image' % name)
            self.segments.append((name, vmaddr, vmsize, fileoff, filesize))
            nsects = struct.unpack_from('<I', data, s + at + 64)[0]
            if 72 + nsects * 80 > size:
                raise MachOError('invalid section table in %s' % name)
            for i in range(nsects):
                row = s + at + 72 + i * 80
                sect = data[row:row + 16].rstrip(b'\0').decode('ascii', 'replace')
                seg = data[row + 16:row + 32].rstrip(b'\0').decode('ascii', 'replace')
                addr, ssize = struct.unpack_from('<QQ', data, row + 32)
                soff = struct.unpack_from('<I', data, row + 48)[0]
                flags = struct.unpack_from('<I', data, row + 64)[0]
                self.sections.append((seg, sect, addr, ssize, soff, flags))

    def first_content(self):
        """Slice offset of the first segment or section content after the header (the end of the padding)."""
        first = self.length
        for _, _, _, fileoff, filesize in self.segments:
            if fileoff and filesize:
                first = min(first, fileoff)
        for _, _, _, size, soff, flags in self.sections:
            if soff and size and flags & 0xff not in ZEROFILL:
                first = min(first, soff)
        return first

    def vm_to_offset(self, addr):
        for name, vmaddr, _, fileoff, filesize in self.segments:
            if filesize and vmaddr <= addr < vmaddr + filesize:
                return self.start + fileoff + addr - vmaddr
        raise MachOError('address %#x is not in a file-backed segment' % addr)

    def section(self, segment, section):
        """(file offset, size) of SEGMENT,section."""
        hits = [(soff, size, flags) for seg, sect, _, size, soff, flags in self.sections
                if seg == segment and sect == section]
        if len(hits) != 1:
            raise MachOError('no single section %s,%s' % (segment, section))
        soff, size, flags = hits[0]
        if flags & 0xff in ZEROFILL or not soff:
            raise MachOError('section %s,%s has no file content' % (segment, section))
        return self.start + soff, size

    def symbols(self):
        """{name: address} of the defined (section) symbols; a name defined twice maps to None."""
        tables = [(at, size) for cmd, at, size in self.commands if cmd == LC_SYMTAB]
        if len(tables) != 1:
            raise MachOError('expected one LC_SYMTAB')
        s, at = self.start, tables[0][0]
        symoff, nsyms, stroff, strsize = struct.unpack_from('<4I', self.data, s + at + 8)
        if symoff + nsyms * 16 > self.length or stroff + strsize > self.length:
            raise MachOError('symbol table extends past the image')
        out = {}
        for i in range(nsyms):
            strx, ntype, _, _, value = struct.unpack_from('<IBBHQ', self.data, s + symoff + i * 16)
            if ntype & 0xe0 or ntype & 0x0e != 0x0e or strx >= strsize:
                continue
            end = self.data.find(b'\0', s + stroff + strx, s + stroff + strsize)
            if end < 0:
                raise MachOError('unterminated symbol name')
            name = self.data[s + stroff + strx:end].decode('utf8', 'replace')
            out[name] = None if name in out else value
        return out

    def symbol_offset(self, name):
        table = self.symbols()
        if name not in table:
            raise MachOError('symbol %s is not defined' % name)
        if table[name] is None:
            raise MachOError('symbol %s is defined more than once' % name)
        return self.vm_to_offset(table[name])

    def dylib_names(self, include_id=False):
        """Dependency paths in load order (and the image's own id with INCLUDE_ID)."""
        out = []
        for cmd, at, size in self.commands:
            if cmd in DEPENDENCY_COMMANDS or (include_id and cmd == LC_ID_DYLIB):
                if size < 24:
                    raise MachOError('truncated dylib command')
                noff = struct.unpack_from('<I', self.data, self.start + at + 8)[0]
                raw = self.data[self.start + at + noff:self.start + at + size]
                if not 24 <= noff < size or b'\0' not in raw:
                    raise MachOError('invalid dylib name')
                out.append((cmd, raw.split(b'\0', 1)[0].decode('utf8')))
        return out


def add_dylib(data, path, weak=False, timestamp=2, current_version=0, compat_version=0):
    """Append LC_LOAD_DYLIB (or LC_LOAD_WEAK_DYLIB) for PATH in the zero padding after the load commands.
    Returns (new data, (start, end) of the new command, info). The code signature is left for the caller to
    replace: re-sign afterwards."""
    if not path.startswith('/') or '\0' in path:
        raise MachOError('dylib path must be absolute')
    img = Image(data)
    if path in [n for _, n in img.dylib_names(include_id=True)]:
        raise MachOError('%s is already referenced' % path)
    before = img.dylib_names()
    name = path.encode('utf8') + b'\0'
    size = (24 + len(name) + 7) & ~7
    end, first = img.header_end, img.first_content()
    s = img.start
    if end + size > first:
        raise MachOError('header padding too small: %d bytes free, %d needed' % (first - end, size))
    if any(data[s + end:s + end + size]):
        raise MachOError('header padding is not zero')
    cmd = LC_LOAD_WEAK_DYLIB if weak else LC_LOAD_DYLIB
    command = struct.pack('<6I', cmd, size, 24, timestamp, current_version, compat_version) + name
    out = bytearray(data)
    out[s + end:s + end + size] = command + bytes(size - len(command))
    ncmds, sizeofcmds = struct.unpack_from('<II', data, s + 16)
    struct.pack_into('<II', out, s + 16, ncmds + 1, sizeofcmds + size)
    out = bytes(out)
    if Image(out).dylib_names() != before + [(cmd, path)]:
        raise MachOError('dependency order not preserved')
    return out, (s + end, s + end + size), {'command': 'LC_LOAD_WEAK_DYLIB' if weak else 'LC_LOAD_DYLIB',
                                             'padding_left': first - end - size}
