"""Dyld cache mapping resolver, adapted from patches/golden-gate-userspace/src/legacy_trackpad.py."""
import struct
from pathlib import Path

def plan_cache_family(directory, image, checker):
    """Return file-offset edits, resolving the image through the cache tables."""
    files, slides = [], []
    for path in sorted(Path(directory).glob('dyld_shared_cache_x86_64*')):
        if path.is_symlink() or path.suffix in ('.map', '.atlas', '.symbols'):
            continue
        with path.open('rb') as stream:
            head = stream.read(512)
            if not head.startswith(b'dyld_v1  x86_64'):
                raise ValueError(f'audio no-console: unexpected cache file {path}')
            offset, count = struct.unpack_from('<II', head, 16)
            stream.seek(offset)
            mappings = [struct.unpack('<QQQII', stream.read(32)) for _ in range(count)]
            # dyld_cache_header.mappingWithSlideOffset/Count: per-mapping slide info
            offset, count = struct.unpack_from('<II', head, 0x138)
            stream.seek(offset)
            for _ in range(count):
                base, length, _, info, info_size = struct.unpack('<QQQQQ', stream.read(56)[:40])
                if info_size:
                    slides.append((base, length, path, info))
        files.append((path, mappings))

    def locate(address, size):
        matches = [(path, file_offset + address - base)
                   for path, mappings in files
                   for base, length, file_offset, _, _ in mappings
                   if base <= address and address + size <= base + length]
        if len(matches) != 1:
            raise ValueError(f'audio no-console: ambiguous/unmapped address {address:#x}')
        return matches[0]

    def read(address, size):
        path, offset = locate(address, size)
        with path.open('rb') as stream:
            stream.seek(offset)
            data = stream.read(size)
        if len(data) != size:
            raise ValueError('audio no-console: truncated cache')
        return data

    def pointer(address):
        """Unslid target of a pointer stored at address (slide info v2 encoding)."""
        raw = struct.unpack('<Q', read(address, 8))[0]
        rows = [(path, info) for base, length, path, info in slides
                if base <= address and address + 8 <= base + length]
        if len(rows) != 1:
            return raw
        path, info = rows[0]
        with path.open('rb') as stream:
            stream.seek(info)
            version, = struct.unpack('<I', stream.read(4))
            if version != 2:
                raise ValueError(f'audio no-console: unsupported slide info v{version}')
            stream.seek(info + 24)
            delta_mask, value_add = struct.unpack('<QQ', stream.read(16))
        value = raw & ~delta_mask & 0xffffffffffffffff
        return value + value_add if value else 0

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
    for address, original, replacement, reason in checker(read, addresses[0], pointer):
        path, offset = locate(address, len(original))
        plan.setdefault(path, []).append((offset, original, replacement, reason))
    return plan


def apply_edits(path, changes):
    """Apply a previously checked plan; refuse intervening changes before writing."""
    with Path(path).open('r+b') as stream:
        for offset, original, _, _ in changes:
            stream.seek(offset)
            if stream.read(len(original)) != original:
                raise ValueError('audio no-console: cache changed after planning')
        for offset, _, replacement, _ in changes:
            stream.seek(offset)
            stream.write(replacement)
