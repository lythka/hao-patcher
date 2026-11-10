"""Combine original signed slices without changing their code or signatures."""
import struct


def slices(data):
    ranges = slice_ranges(data)
    return None if ranges is None else {arch: data[start:start+size] for arch, (start, size) in ranges.items()}


def slice_ranges(data):
    """{(cputype, cpusubtype): (start, size)} of a thin or universal Mach-O, or None."""
    data = memoryview(data).cast('B')
    if bytes(data[:4]) in (b'\xcf\xfa\xed\xfe', b'\xce\xfa\xed\xfe'):
        return {struct.unpack_from('<II', data, 4): (0, len(data))}
    formats = {b'\xca\xfe\xba\xbe': ('>', False), b'\xbe\xba\xfe\xca': ('<', False),
               b'\xca\xfe\xba\xbf': ('>', True), b'\xbf\xba\xfe\xca': ('<', True)}
    magic = bytes(data[:4])
    if magic not in formats:
        return None
    order, wide = formats[magic]
    count, = struct.unpack_from(order+'I', data, 4)
    entry = 32 if wide else 20
    if count > (len(data)-8)//entry:
        raise ValueError('Truncated universal Mach-O header')
    result = {}
    for i in range(count):
        offset = 8+i*entry
        cpu, subtype = struct.unpack_from(order+'II', data, offset)
        start, size = struct.unpack_from(order+('QQ' if wide else 'II'), data, offset+8)
        if start < 8+count*entry or start+size > len(data):
            raise ValueError('Mach-O slice outside file')
        result[(cpu, subtype)] = (start, size)
    return result


def section_ranges(data, segment, section):
    """{arch: (file offset, size)} of SEGMENT,SECTION in each 64-bit slice that has it."""
    result = {}
    for arch, (start, _) in (slice_ranges(data) or {}).items():
        if data[start:start+4] != b'\xcf\xfa\xed\xfe':
            continue
        ncmds, = struct.unpack_from('<I', data, start+16)
        at = start+32
        for _ in range(ncmds):
            cmd, size = struct.unpack_from('<II', data, at)
            if cmd == 0x19 and data[at+8:at+24].rstrip(b'\0') == segment:
                nsects, = struct.unpack_from('<I', data, at+64)
                for i in range(nsects):
                    sect = at+72+i*80
                    if data[sect:sect+16].rstrip(b'\0') == section:
                        length, offset = struct.unpack_from('<QI', data, sect+40)
                        result[arch] = (start+offset, length)
            at += size
    return result


def combine(base, additional):
    first, second = slices(base), slices(additional)
    if first is None or second is None:
        return None
    extra = {key: value for key, value in second.items() if key not in first}
    if not extra:
        return base
    first.update(extra)
    result = bytearray(struct.pack('>II', 0xcafebabe, len(first)) + bytes(20*len(first)))
    for i, ((cpu, subtype), body) in enumerate(first.items()):
        start = (len(result)+16383) & ~16383
        result.extend(bytes(start-len(result)))
        result.extend(body)
        struct.pack_into('>IIIII', result, 8+i*20, cpu, subtype, start, len(body), 14)
    return bytes(result)


def add_load_dylib(data, path, cputype):
    """Append LC_LOAD_DYLIB PATH to the slice of CPUTYPE in place, in the zero padding between the load commands
    and the first section. The code signature is invalid afterwards; the caller re-signs."""
    for (cpu, _), (start, _) in slice_ranges(data).items():
        if cpu != cputype:
            continue
        ncmds, sizeofcmds = struct.unpack_from('<II', data, start+16)
        first, at = None, start+32
        for _ in range(ncmds):
            cmd, size = struct.unpack_from('<II', data, at)
            if cmd in (0xc, 0x80000018) and data[at+24:at+size].split(b'\0', 1)[0] == path:
                return False
            if cmd == 0x19:
                nsects, = struct.unpack_from('<I', data, at+64)
                for i in range(nsects):
                    offset, = struct.unpack_from('<I', data, at+72+i*80+48)
                    if offset and (first is None or offset < first):
                        first = offset
            at += size
        name = path+b'\0'
        size = (24+len(name)+7)//8*8
        end = start+32+sizeofcmds
        if first is None or end+size > start+first or any(data[end:end+size]):
            raise ValueError('no room for another load command')
        data[end:end+size] = struct.pack('<IIIIII', 0xc, size, 24, 2, 0x10000, 0x10000)+name.ljust(size-24, b'\0')
        struct.pack_into('<II', data, start+16, ncmds+1, sizeofcmds+size)
        return True
    raise ValueError('no slice of cpu type %#x' % cputype)


def set_install_name(data, old, new):
    """Change a thin x86_64 dylib's LC_ID_DYLIB from OLD to NEW (bytes) in place, growing the command into the zero
    padding after the load commands when NEW is longer; the later commands move down (no load command holds an
    offset into the header). Stands in for install_name_tool -id, which the builder VM lacks; the caller re-signs."""
    magic, cpu, _, kind, ncmds, sizeofcmds = struct.unpack_from('<6I', data, 0)
    if magic != 0xfeedfacf or cpu != 0x1000007 or kind != 6:
        raise ValueError('not a thin x86_64 dylib')
    first, found, at = None, None, 32
    for _ in range(ncmds):
        cmd, size = struct.unpack_from('<II', data, at)
        if cmd == 0x19:
            nsects, = struct.unpack_from('<I', data, at+64)
            for i in range(nsects):
                offset, = struct.unpack_from('<I', data, at+72+i*80+48)
                if offset and (first is None or offset < first):
                    first = offset
        if cmd == 0xd:
            found = at, size
        at += size
    if found is None:
        raise ValueError('no LC_ID_DYLIB')
    at, size = found
    name_at, = struct.unpack_from('<I', data, at+8)
    current = bytes(data[at+name_at:at+size]).split(b'\0', 1)[0]
    if current != old:
        raise ValueError('install name is %s, expected %s' % (current.decode(), old.decode()))
    grown = max(size, (name_at+len(new)+1+7)//8*8)
    end = 32+sizeofcmds
    if first is None or end+grown-size > first or any(data[end:end+grown-size]):
        raise ValueError('no room for the longer install name')
    data[at:end+grown-size] = bytes(data[at:at+name_at])+new.ljust(grown-name_at, b'\0')+bytes(data[at+size:end])
    struct.pack_into('<I', data, at+4, grown)
    struct.pack_into('<I', data, 20, sizeofcmds+grown-size)
