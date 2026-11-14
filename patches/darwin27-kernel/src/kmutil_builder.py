"""Make KMUTIL (Tahoe's, universal or thin) a thin x86_64 kmutil that loads its kernel-collection builder from PATH
instead of /usr/lib/libKernelCollectionBuilder.dylib. Used on an Intel host, where the running system's builder is
Golden Gate's and refuses x86_64; the hook puts Tahoe's (inputs/tahoe_kc_builder) at PATH. No developer tools: the
dylib name is rewritten in place inside its load command (PATH must fit there). Sign the result after.

  python3 kmutil_builder.py KMUTIL PATH
"""
import struct
import sys

BUILDER = b'/usr/lib/libKernelCollectionBuilder.dylib'
FAT_MAGIC, FAT_MAGIC_64, MH_MAGIC_64 = 0xCAFEBABE, 0xCAFEBABF, 0xFEEDFACF
CPU_TYPE_X86_64 = 0x01000007
LOAD_COMMANDS = (0xC, 0x80000018, 0x8000001F, 0x80000023)   # LC_LOAD_DYLIB, _WEAK_, _REEXPORT_, _UPWARD_


def x86_64_slice(data):
    """The thin x86_64 Mach-O inside DATA (DATA itself when it is one)."""
    magic, = struct.unpack_from('>I', data)
    if magic in (FAT_MAGIC, FAT_MAGIC_64):
        n, = struct.unpack_from('>I', data, 4)
        for i in range(n):
            if magic == FAT_MAGIC:
                cpu, _, offset, size, _ = struct.unpack_from('>iiIII', data, 8 + 20*i)
            else:
                cpu, _, offset, size, _, _ = struct.unpack_from('>iiQQII', data, 8 + 32*i)
            if cpu == CPU_TYPE_X86_64:
                return bytearray(data[offset:offset + size])
        raise SystemExit('kmutil has no x86_64 slice')
    if struct.unpack_from('<Ii', data) == (MH_MAGIC_64, CPU_TYPE_X86_64):
        return bytearray(data)
    raise SystemExit('kmutil is not an x86_64 Mach-O')


def repoint(macho, path):
    """Rewrite the builder's dylib load command in MACHO (thin x86_64) to load PATH; returns how many it changed."""
    ncmds, = struct.unpack_from('<I', macho, 16)
    at, changed = 32, 0
    for _ in range(ncmds):
        cmd, size = struct.unpack_from('<II', macho, at)
        if cmd in LOAD_COMMANDS:
            name_at, = struct.unpack_from('<I', macho, at + 8)
            start, end = at + name_at, at + size
            if bytes(macho[start:end]).split(b'\0', 1)[0] == BUILDER:
                if len(path) + 1 > end - start:
                    raise SystemExit('%s does not fit the load command (%d bytes)' % (path.decode(), end - start))
                macho[start:end] = path + b'\0' * (end - start - len(path))
                changed += 1
        at += size
    return changed


def main(kmutil, path):
    with open(kmutil, 'rb') as f:
        macho = x86_64_slice(f.read())
    if repoint(macho, path.encode()) != 1:
        raise SystemExit('kmutil does not load %s once' % BUILDER.decode())
    with open(kmutil, 'wb') as f:
        f.write(macho)


if __name__ == '__main__':
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    main(sys.argv[1], sys.argv[2])
