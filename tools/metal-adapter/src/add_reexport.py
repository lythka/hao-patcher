"""add_reexport.py IN OUT DYLIB_PATH: append an LC_REEXPORT_DYLIB load command to an unsigned x86_64 dylib.

Used once: the bundle's Metal27 (cache-dylib cut of 27.2.1's Metal) re-exports libHaoMetalShim.dylib, so clients that
bind symbols from Metal find the shim's ivar offsets and stub through Metal itself. The command goes after every
existing one (so existing dylib ordinals do not move) into the header padding; 16 bytes stay free for codesign's
LC_CODE_SIGNATURE. Refuses a signed input, a fat file, or too little room."""
import struct
import sys

MH_MAGIC_64, LC_REEXPORT_DYLIB, LC_CODE_SIGNATURE, LC_SEGMENT_64 = 0xFEEDFACF, 0x8000001F, 0x1D, 0x19


def main(src, dst, path):
    data = bytearray(open(src, 'rb').read())
    magic, cputype, _, _, ncmds, sizeofcmds, _, _ = struct.unpack_from('<IiiIIIII', data, 0)
    if magic != MH_MAGIC_64 or cputype != 0x01000007:
        sys.exit('not a thin x86_64 Mach-O')
    at, first_section = 32, None
    for _ in range(ncmds):
        cmd, size = struct.unpack_from('<II', data, at)
        if cmd == LC_CODE_SIGNATURE:
            sys.exit('input is signed; give the unsigned cut')
        if cmd == LC_SEGMENT_64:
            nsects, = struct.unpack_from('<I', data, at + 64)
            for i in range(nsects):
                off, = struct.unpack_from('<I', data, at + 72 + i * 80 + 48)
                size_s, = struct.unpack_from('<Q', data, at + 72 + i * 80 + 40)
                if off and size_s and (first_section is None or off < first_section):
                    first_section = off
        at += size
    name = path.encode() + b'\0'
    body = struct.pack('<IIIIII', LC_REEXPORT_DYLIB, 0, 24, 0, 0x10000, 0x10000) + name
    body += b'\0' * (-len(body) % 8)
    body = body[:4] + struct.pack('<I', len(body)) + body[8:]
    end = 32 + sizeofcmds
    if end + len(body) + 16 > first_section or any(data[end:end + len(body) + 16]):
        sys.exit(f'no room: commands end at {end:#x}, first section at {first_section:#x}')
    data[end:end + len(body)] = body
    struct.pack_into('<II', data, 16, ncmds + 1, sizeofcmds + len(body))
    open(dst, 'wb').write(data)
    print(f'added LC_REEXPORT_DYLIB {path} ({len(body)} bytes); room left {first_section - end - len(body)}')


if __name__ == '__main__':
    main(*sys.argv[1:4])
