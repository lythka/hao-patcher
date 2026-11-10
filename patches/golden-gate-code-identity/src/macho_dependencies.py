"""Append explicit dependencies to an ad-hoc x86 executable, in place by slice.

No signature policy is relaxed: only existing code-page hashes are refreshed.
Layout follows hao_fat.py/hao_codesig.py and Apple's Mach-O/code-signing headers.
"""
import hashlib
import struct

X86 = 0x1000007
DYLIB_COMMANDS = {0xc, 0x80000018, 0x8000001f, 0x80000023, 0x20}


def digest(kind, data):
    names = {1: 'sha1', 2: 'sha256', 3: 'sha256', 4: 'sha384'}
    if kind not in names:
        raise ValueError('unsupported CodeDirectory hash type')
    value = hashlib.new(names[kind], data).digest()
    return value[:20] if kind == 3 else value


def x86_range(data):
    if len(data) < 32:
        raise ValueError('truncated Mach-O')
    if data[:4] == b'\xcf\xfa\xed\xfe':
        if struct.unpack_from('<I', data, 4)[0] != X86:
            raise ValueError('expected x86_64 executable')
        return 0, len(data)
    magic, count = struct.unpack_from('>II', data)
    if magic not in (0xcafebabe, 0xcafebabf):
        raise ValueError('unsupported universal header')
    wide = magic == 0xcafebabf
    stride = 32 if wide else 20
    table_end = 8 + count * stride
    if not count or count > 64 or table_end > len(data):
        raise ValueError('invalid universal slice table')
    regions, found = [], []
    for i in range(count):
        at = 8 + i * stride
        cpu = struct.unpack_from('>I', data, at)[0]
        start, length = struct.unpack_from('>QQ' if wide else '>II', data, at + 8)
        if start < table_end or length < 32 or start + length > len(data):
            raise ValueError('invalid universal slice range')
        if any(start < b and a < start + length for a, b in regions):
            raise ValueError('overlapping universal slices')
        regions.append((start, start + length))
        if cpu == X86:
            found.append((start, length))
    if len(found) != 1:
        raise ValueError('expected exactly one x86_64 slice')
    return found[0]


def load_commands(mo, filetype=2):
    if len(mo) < 32:
        raise ValueError('truncated Mach-O')
    magic, cpu, _, kind, count, size, _, _ = struct.unpack_from('<8I', mo)
    if magic != 0xfeedfacf or cpu != X86 or kind != filetype:
        raise ValueError(f'expected native x86_64 Mach-O file type {filetype}')
    end = 32 + size
    if end > len(mo) or count > size // 8:
        raise ValueError('invalid load command bounds')
    out, at = [], 32
    for _ in range(count):
        if at + 8 > end:
            raise ValueError('truncated load command')
        cmd, length = struct.unpack_from('<II', mo, at)
        if length < 8 or length % 8 or at + length > end:
            raise ValueError('invalid load command')
        out.append((cmd, at, length))
        at += length
    if at != end:
        raise ValueError('load command size mismatch')
    return out


def dependencies(mo, commands):
    out = []
    for cmd, at, size in commands:
        if cmd in DYLIB_COMMANDS:
            if size < 24:
                raise ValueError('truncated dylib command')
            offset = struct.unpack_from('<I', mo, at + 8)[0]
            if not 24 <= offset < size or b'\0' not in mo[at + offset:at + size]:
                raise ValueError('invalid dylib name')
            name = mo[at + offset:at + size].split(b'\0', 1)[0].decode('utf8')
            out.append((cmd, name))
    return out


def signature_directories(mo, commands, *, require_adhoc=True):
    signatures = [(at, size) for cmd, at, size in commands if cmd == 0x1d]
    if len(signatures) != 1 or signatures[0][1] != 16:
        raise ValueError('expected one embedded signature')
    base, size = struct.unpack_from('<II', mo, signatures[0][0] + 8)
    if size < 12 or base + size > len(mo):
        raise ValueError('invalid signature range')
    magic, length, count = struct.unpack_from('>III', mo, base)
    if magic != 0xfade0cc0 or length > size or 12 + count * 8 > length:
        raise ValueError('invalid signature SuperBlob')
    cds, types, intervals = [], set(), []
    for i in range(count):
        slot, offset = struct.unpack_from('>II', mo, base + 12 + i * 8)
        if slot in types or offset < 12 + count * 8 or offset + 8 > length:
            raise ValueError('invalid signature index')
        types.add(slot)
        bmagic, blen = struct.unpack_from('>II', mo, base + offset)
        if blen < 8 or offset + blen > length:
            raise ValueError('invalid signature blob')
        if any(offset < b and a < offset + blen for a, b in intervals):
            raise ValueError('overlapping signature blobs')
        intervals.append((offset, offset + blen))
        if require_adhoc and slot == 0x10000 and (bmagic != 0xfade0b01 or blen != 8):
            raise ValueError('CMS-signed executables require their signing identity; refused')
        if slot != 0 and not 0x1000 <= slot < 0x1005:
            continue
        cd = base + offset
        if bmagic != 0xfade0c02 or blen < 48:
            raise ValueError('invalid CodeDirectory')
        version, flags, hashes, identifier, special, pages, limit = struct.unpack_from('>7I', mo, cd + 8)
        hsize, kind, _, exponent = mo[cd + 36:cd + 40]
        if require_adhoc and not flags & 2:
            raise ValueError('only existing ad-hoc signatures can be refreshed')
        if not 0x20100 <= version <= 0x20600 or struct.unpack_from('>I', mo, cd + 44)[0]:
            raise ValueError('unsupported CodeDirectory version/scatter layout')
        minimum = next(size for threshold, size in [(0x20600,108),(0x20500,96),(0x20400,88),(0x20300,64),(0x20200,52),(0x20100,48)] if version >= threshold)
        if blen < minimum or hashes - special * hsize < minimum:
            raise ValueError('CodeDirectory hashes overlap metadata')
        if not minimum <= identifier < hashes-special*hsize or b'\0' not in mo[cd+identifier:cd+hashes-special*hsize]:
            raise ValueError('invalid CodeDirectory identifier')
        if version >= 0x20300:
            if blen < 64 or struct.unpack_from('>Q', mo, cd + 56)[0]:
                raise ValueError('unsupported nonzero 64-bit code limit')
        if exponent > 30 or limit != base or not limit:
            raise ValueError('unsupported code-page coverage')
        page_size = (1 << exponent) if exponent else limit
        if pages != (limit + page_size - 1) // page_size:
            raise ValueError('invalid code-page count')
        if hsize != len(digest(kind, b'')) or hashes < special * hsize or hashes + pages * hsize > blen:
            raise ValueError('invalid CodeDirectory hash ranges')
        for page in range(pages):
            expected = digest(kind, mo[page * page_size:min((page + 1) * page_size, limit)])
            if mo[cd + hashes + page * hsize:cd + hashes + (page + 1) * hsize] != expected:
                raise ValueError('original code-page hash mismatch')
        cds.append((cd, hashes, pages, limit, page_size, kind, hsize, flags, special))
    if 0 not in types or not cds:
        raise ValueError('missing primary CodeDirectory')
    return cds


def append_dylibs(data, paths, *, resign_filetype=None):
    if not paths or len(set(paths)) != len(paths):
        raise ValueError('provide unique dylib paths')
    for path in paths:
        if not path.startswith('/') or '\0' in path or len(path.encode('utf8')) > 4095:
            raise ValueError('dependency must be an explicit absolute path')
    start, length = x86_range(data)
    if resign_filetype is not None and (start != 0 or length != len(data)):
        raise ValueError('re-signing mode requires a thin image')
    mo = data[start:start + length]
    if resign_filetype not in (None, 2, 6):
        raise ValueError('re-signing mode supports executable or dylib only')
    filetype = resign_filetype or 2
    commands = load_commands(mo, filetype)
    old_dependencies = dependencies(mo, commands)
    if any(path in {name for _, name in old_dependencies} for path in paths):
        raise ValueError('dependency already present')
    # Executable mode preserves the existing ad-hoc policy. Explicit dylib
    # packaging instead leaves the invalidated signature for the caller's signer;
    # it cannot preserve a CMS identity whose private key we do not possess.
    cds = [] if resign_filetype is not None else signature_directories(mo, commands)
    end = 32 + struct.unpack_from('<I', mo, 20)[0]
    first = len(mo)
    for cmd, at, size in commands:
        if cmd != 0x19:
            continue
        if size < 72:
            raise ValueError('truncated segment')
        fileoff, filesize = struct.unpack_from('<QQ', mo, at + 40)
        if fileoff and filesize:
            first = min(first, fileoff)
        count = struct.unpack_from('<I', mo, at + 64)[0]
        if 72 + count * 80 > size:
            raise ValueError('invalid section table')
        for i in range(count):
            section = at + 72 + i * 80
            offset = struct.unpack_from('<I', mo, section + 48)[0]
            stype = struct.unpack_from('<I', mo, section + 64)[0] & 0xff
            section_size = struct.unpack_from('<Q', mo, section + 40)[0]
            if offset and section_size and stype not in (1, 0xc, 0x12):
                first = min(first, offset)
    added = bytearray()
    for path in paths:
        name = path.encode('utf8') + b'\0'
        size = (24 + len(name) + 7) & ~7
        command = struct.pack('<6I', 0xc, size, 24, 0, 0, 0) + name
        added.extend(command + bytes(size - len(command)))
    if first <= end or end + len(added) > first or any(mo[end:end + len(added)]):
        raise ValueError('insufficient zero-filled header padding')
    out = bytearray(mo)
    out[end:end + len(added)] = added
    count, size = struct.unpack_from('<II', mo, 16)
    struct.pack_into('<II', out, 16, count + len(paths), size + len(added))
    for cd, hashes, pages, limit, page_size, kind, hsize, _, _ in cds:
        for page in range(pages):
            out[cd + hashes + page * hsize:cd + hashes + (page + 1) * hsize] = digest(kind, out[page * page_size:min((page + 1) * page_size, limit)])
    # Validate the resulting signature; all metadata/special slots were untouched.
    if resign_filetype is None:
        signature_directories(out, load_commands(out))
    if dependencies(out, load_commands(out, filetype)) != old_dependencies + [(0xc, x) for x in paths]:
        raise ValueError('dependency ordinal preservation failed')
    report = {'slice_offset': start, 'slice_size': length, 'added_dependencies': paths,
              'existing_dependencies_preserved': len(old_dependencies), 'header_padding_remaining': first-end-len(added),
              'code_directory_flags': [hex(c[7]) for c in cds],
              'special_slots_unchanged': True, 'entitlement_blobs_unchanged': True,
              'other_slices_byte_identical': True, 'signing_method': 'refresh existing ad-hoc code-page hashes only'}
    if resign_filetype is not None:
        report = {'slice_offset': 0, 'slice_size': length, 'added_dependencies': paths,
                  'existing_dependencies_preserved': len(old_dependencies),
                  'header_padding_remaining': first-end-len(added),
                  'requires_resigning': True,
                  'signing_method': 'existing signature invalidated; caller must sign'}
    return data[:start] + out + data[start + length:], report
