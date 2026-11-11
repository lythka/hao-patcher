"""Restore the retained V3 touch decoder in 26A428's native MultitouchSupport.

Tahoe's AppleUSBMultitouchDriver supplies report 0x74. Golden Gate removed that
dispatch case and restricted both surviving V3 decoder functions to internal OS
variants. Restore the 0x73/0x74 dispatch used by Tahoe and bypass those two gates.
The decoded 0x74 frame must also reach contact processing, and legacy header
callbacks need their V3 conversion gates removed. All decoder bounds checks
and other packet handlers remain Apple's code.
The caller must re-sign changed cache files before sealing the cryptex.
"""
import struct
from pathlib import Path

IMAGE = b'/System/Library/PrivateFrameworks/MultitouchSupport.framework/Versions/A/MultitouchSupport'
def image_layout(read, base):
    """Read executable sections from the supplied cache image, without a build allowlist."""
    header = read(base, 32)
    if len(header) != 32 or struct.unpack_from('<II', header) != (0xfeedfacf, 0x1000007):
        raise ValueError('MISSING/unsupported: native x86_64 Mach-O')
    count, size = struct.unpack_from('<II', header, 16)
    commands = read(base+32, size)
    at, text, sections = 0, None, {}
    for _ in range(count):
        if at+8 > len(commands):
            raise ValueError('MISSING/unsupported: truncated load commands')
        command, length = struct.unpack_from('<II', commands, at)
        if length < 8 or at+length > len(commands):
            raise ValueError('MISSING/unsupported: invalid load command')
        if command == 0x19 and commands[at+8:at+24].rstrip(b'\0') == b'__TEXT':
            text = struct.unpack_from('<Q', commands, at+24)[0]
            for i in range(struct.unpack_from('<I', commands, at+64)[0]):
                row = at+72+i*80
                if row+80 > at+length:
                    raise ValueError('MISSING/unsupported: invalid section table')
                sections[commands[row:row+16].rstrip(b'\0')] = struct.unpack_from('<QQ', commands, row+32)
        at += length
    if text != base or b'__text' not in sections:
        raise ValueError('MISSING/unsupported: __TEXT/__text layout')
    return size+32, sections


def code_sites(read, base, image):
    """Require one match for every instruction recipe before returning any edits.

    Relocated branch and RIP-relative operands are wildcards; opcodes, register
    choices and selector/packet constants remain exact. Recipes are shared by
    26A428 and 26B131. An absent or ambiguous shape rejects the whole image.
    """
    import json
    import re
    _, sections = image_layout(read, base)
    start, size = sections[b'__text']
    code = read(start, size)
    recipes = json.loads(Path(__file__).with_name('compat-locators.json').read_text())[image]
    found = []
    for recipe in recipes:
        pattern = b''.join(b'.' if token == '??' else re.escape(bytes.fromhex(token))
                           for token in recipe['pattern'].split())
        hits = list(re.finditer(b'(?=('+pattern+b'))', code, re.S))
        if len(hits) != 1:
            raise ValueError('MISSING/unsupported: %s %s: expected one code shape, found %d'
                             % (image, recipe['reason'], len(hits)))
        address = start+hits[0].start()+recipe['offset']
        found.append((address, read(address, recipe['length']), recipe['reason']))
    return found


def checked_edits(read, image_address):
    sites = code_sites(read, image_address, 'MultitouchSupport')
    result = []
    for i, (address, original, reason) in enumerate(sites):
        if i == 0:
            # Preserve the original other-packet destination when adding report 0x74.
            target = address+5+struct.unpack('b', original[4:5])[0]
            displacement = target-(address+8)
            replacement = bytes.fromhex('8d518d83fa0177')+struct.pack('b', displacement)+b'\x90'*13
        elif i == 3:
            # Both original range/bit-test failures must reach the same fallback.
            fallback = address+17+struct.unpack('<i', original[13:17])[0]
            if fallback != address+31+struct.unpack('<i', original[27:31])[0]:
                raise ValueError('MISSING/unsupported: trackpad contact fallback paths differ')
            replacement = bytes.fromhex('410fb647083c7474162c283c1c770ab9030600100fa3c17206e9')
            replacement += struct.pack('<i', fallback-(address+30))+b'\x90'
        else:
            replacement = b'\x90'*len(original)
        result.append((address, original, replacement, reason))
    return result


def plan_cache_family(directory, image=IMAGE, checker=checked_edits):
    """Return file-offset edits, resolving the image through the cache tables."""
    files = []
    for path in sorted(Path(directory).glob('dyld_shared_cache_x86_64*')):
        if path.is_symlink() or path.suffix in ('.map', '.atlas', '.symbols'):
            continue
        with path.open('rb') as stream:
            head = stream.read(512)
            if not head.startswith(b'dyld_v1  x86_64'):
                raise ValueError(f'legacy trackpad: unexpected cache file {path}')
            offset, count = struct.unpack_from('<II', head, 16)
            stream.seek(offset)
            mappings = [struct.unpack('<QQQII', stream.read(32)) for _ in range(count)]
        files.append((path, mappings))

    def locate(address, size):
        matches = [(path, file_offset + address - base)
                   for path, mappings in files
                   for base, length, file_offset, _, _ in mappings
                   if base <= address and address + size <= base + length]
        if len(matches) != 1:
            raise ValueError(f'legacy trackpad: ambiguous/unmapped address {address:#x}')
        return matches[0]

    def read(address, size):
        path, offset = locate(address, size)
        with path.open('rb') as stream:
            stream.seek(offset)
            data = stream.read(size)
        if len(data) != size:
            raise ValueError('legacy trackpad: truncated cache')
        return data

    main = Path(directory)/'dyld_shared_cache_x86_64'
    addresses = []
    with main.open('rb') as stream:
        stream.seek(448)  # modern dyld_cache_header.imagesOffset/imagesCount
        offset, count = struct.unpack('<II', stream.read(8))
        stream.seek(offset)
        table = stream.read(count * 32)
        for row in range(count):
            address, _, _, name_offset, _ = struct.unpack_from('<QQQII', table, row * 32)
            stream.seek(name_offset)
            if stream.read(len(image) + 1) == image + b'\0':
                addresses.append(address)
    if len(addresses) != 1:
        raise ValueError('expected one cached image: ' + image.decode())
    plan = {}
    for address, original, replacement, reason in checker(read, addresses[0]):
        path, offset = locate(address, len(original))
        plan.setdefault(path, []).append((offset, original, replacement, reason))
    return plan


def apply_edits(path, changes):
    """Apply a previously checked plan; refuse intervening changes before writing."""
    with Path(path).open('r+b') as stream:
        for offset, original, _, _ in changes:
            stream.seek(offset)
            if stream.read(len(original)) != original:
                raise ValueError('legacy trackpad: cache changed after planning')
        for offset, _, replacement, _ in changes:
            stream.seek(offset)
            stream.write(replacement)
