#!/usr/bin/env python3
"""add_weak_dylib.py IN OUT DYLIB_PATH: copy a thin x86_64 Mach-O dylib and append one LC_LOAD_WEAK_DYLIB for
DYLIB_PATH in the zero padding after the load commands (refuses if the padding is too small or not all zero, or if
the path is already referenced). The old code signature is left in place for `codesign -f -s -` to replace.
Same as patches/golden-gate-hello/src/add_weak_dylib.py (the HD 4000 MTL driver bundle executable is an MH_DYLIB)."""
import struct, sys
LC_SEGMENT_64, LC_LOAD_WEAK_DYLIB = 0x19, 0x80000018
DYLIB_CMDS = {0xc, 0x80000018, 0x8000001f, 0x80000023, 0xd}   # load, weak, reexport, lazy, id
def main(src, dst, path):
    b = bytearray(open(src, 'rb').read())
    magic, cpu, sub, ftype, ncmds, sizeofcmds, flags, _ = struct.unpack_from('<8I', b, 0)
    assert magic == 0xfeedfacf and cpu == 0x01000007, 'not a thin x86_64 Mach-O'
    assert ftype == 6, 'not a dylib (MH_DYLIB)'
    off, first_sect = 32, None
    for _ in range(ncmds):
        cmd, size = struct.unpack_from('<2I', b, off)
        if cmd == LC_SEGMENT_64:
            nsects = struct.unpack_from('<I', b, off + 64)[0]
            for s in range(nsects):
                so = off + 72 + s * 80
                soff = struct.unpack_from('<I', b, so + 48)[0]
                if soff and (first_sect is None or soff < first_sect): first_sect = soff
        if cmd in DYLIB_CMDS:
            noff = struct.unpack_from('<I', b, off + 8)[0]
            name = bytes(b[off + noff:off + size]).split(b'\0')[0].decode()
            assert name != path, f'{path} is already referenced'
        off += size
    end = 32 + sizeofcmds
    assert off == end, 'load command walk mismatch'
    name = path.encode() + b'\0'
    cmdsize = (24 + len(name) + 7) & ~7
    assert first_sect and end + cmdsize <= first_sect, f'padding too small: {first_sect - end} < {cmdsize}'
    assert not any(b[end:end + cmdsize]), 'padding is not zero'
    lc = struct.pack('<6I', LC_LOAD_WEAK_DYLIB, cmdsize, 24, 2, 0, 0) + name
    b[end:end + cmdsize] = lc + b'\0' * (cmdsize - len(lc))
    struct.pack_into('<2I', b, 16, ncmds + 1, sizeofcmds + cmdsize)
    open(dst, 'wb').write(b)
    print(f'{dst}: + LC_LOAD_WEAK_DYLIB {path} (cmdsize {cmdsize}) at {end:#x}; padding left {first_sect - end - cmdsize}')
if __name__ == '__main__':
    main(*sys.argv[1:4])
