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
import uuid
from pathlib import Path

IMAGE = b'/System/Library/PrivateFrameworks/MultitouchSupport.framework/Versions/A/MultitouchSupport'
UUID = uuid.UUID('C3AA1BE3-C6A4-3A3B-ACDE-EFF9F71E2CA9').bytes


def edits():
    """(TEXT-relative offset, original instructions, replacement, reason)."""
    # lea -0x73(%rcx),%edx; cmp $1,%edx; ja other_packet; nop to V3 call.
    # The following original instructions set every argument for the V3 parser.
    dispatch = bytes.fromhex('8d518d 83fa01 7727') + b'\x90' * 13
    # The downstream selector lost Tahoe's explicit 0x74 contact-frame case.
    # Fit it in the original block using short branches to its path/fallback
    # exits. AL subtraction is safe: range checking rejects wrapped values.
    # movzbl 8(r15),eax; cmp al,0x74; je path; sub al,0x28; cmp al,0x1c;
    # ja fallback; mov ecx,0x10000603; bt ecx,eax; jc path; jmp other; nop.
    route = bytes.fromhex('410fb64708 3c74 7416 2c28 3c1c 770a b903060010 0fa3c1 7206 e9')
    route += struct.pack('<i', 0xd227 - (0xd090 + 30)) + b'\x90'
    return [
        (0xceca, bytes.fromhex('83f973752a488d3dca280100e8655b030084c07416'),
         dispatch, 'dispatch V3 reports 0x73 and 0x74'),
        (0xf37b, bytes.fromhex('0f8422010000'), b'\x90' * 6,
         'allow the V3 frame header on public OS variants'),
        (0xf56a, bytes.fromhex('0f849e020000'), b'\x90' * 6,
         'allow V3 contact decoding on public OS variants'),
        (0xd090, bytes.fromhex('410fb6470883c0d883f81c0f8786010000b9030600100fa3c10f8378010000'),
         route, 'send decoded 0x74 frames to contact processing'),
        (0xdca4, bytes.fromhex('7475'), b'\x90' * 2,
         'deliver legacy V3 frame header callbacks'),
        (0x10ddb, bytes.fromhex('7461'), b'\x90' * 2,
         'convert legacy V3 headers for callbacks'),
    ]


def checked_edits(read, image_address):
    """Validate identity and every instruction before returning any writes."""
    header = read(image_address, 32)
    magic, cpu = struct.unpack_from('<II', header)
    if (magic, cpu) != (0xfeedfacf, 0x1000007):
        raise ValueError('legacy trackpad: expected x86_64 Mach-O')
    count, size = struct.unpack_from('<II', header, 16)
    commands = read(image_address + 32, size)
    at, identity, text = 0, None, None
    for _ in range(count):
        command, length = struct.unpack_from('<II', commands, at)
        if length < 8 or at + length > len(commands):
            raise ValueError('legacy trackpad: invalid load command')
        if command == 0x1b:
            identity = commands[at + 8:at + 24]
        if command == 0x19 and commands[at + 8:at + 24].rstrip(b'\0') == b'__TEXT':
            text = struct.unpack_from('<Q', commands, at + 24)[0]
        at += length
    if identity != UUID or text != image_address:
        raise ValueError('legacy trackpad: unsupported MultitouchSupport UUID/layout')
    result = []
    for offset, original, replacement, reason in edits():
        address = text + offset
        if read(address, len(original)) != original:
            raise ValueError(f'legacy trackpad: instructions differ at TEXT+{offset:#x}')
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
