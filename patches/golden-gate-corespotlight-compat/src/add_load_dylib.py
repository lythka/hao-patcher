#!/usr/bin/env python3
"""add_load_dylib.py IN OUT DYLIB: copy a Mach-O (thin or universal) and append one LC_LOAD_DYLIB for DYLIB to its
x86_64 slice, in the zero padding after the load commands; other slices are unchanged. Refuses if the padding is
too small or not zero, or if DYLIB is already referenced. The caller re-signs (the old signature is left in place).
Adapted from patches/golden-gate-windowserver-filter/src/load_shim.py (which edits a dylib) for an executable."""
import struct
import sys

LC_SEGMENT_64, LC_LOAD_DYLIB = 0x19, 0xc
DYLIB_CMDS = {0xc, 0x80000018, 0x8000001f, 0x80000023, 0xd}


def slices(data):
    if data[:4] == b'\xca\xfe\xba\xbe':
        return [struct.unpack_from('>IIIII', data, 8+20*i)[2] for i in range(struct.unpack_from('>I', data, 4)[0])]
    if data[:4] == b'\xcf\xfa\xed\xfe':
        return [0]
    raise ValueError('not a Mach-O')


def add(data, path):
    data, name, done = bytearray(data), path.encode()+b'\0', 0
    for start in slices(data):
        magic, cpu, _, kind, ncmds, sizeofcmds = struct.unpack_from('<6I', data, start)
        if magic != 0xfeedfacf or cpu != 0x1000007:
            continue
        at, first = start+32, None
        for _ in range(ncmds):
            cmd, size = struct.unpack_from('<II', data, at)
            if cmd == LC_SEGMENT_64:
                for k in range(struct.unpack_from('<I', data, at+64)[0]):
                    offset = struct.unpack_from('<I', data, at+72+80*k+48)[0]
                    if offset and (first is None or offset < first):
                        first = offset
            if cmd in DYLIB_CMDS:
                text = bytes(data[at+struct.unpack_from('<I', data, at+8)[0]:at+size]).split(b'\0')[0]
                if text == path.encode():
                    raise ValueError(f'{path} is already referenced')
            at += size
        end = start+32+sizeofcmds
        if at != end:
            raise ValueError('load command walk mismatch')
        size = (24+len(name)+7) & ~7
        if first is None or 32+sizeofcmds+size > first:
            raise ValueError(f'padding too small: {(first or 0)-32-sizeofcmds} < {size}')
        if any(data[end:end+size]):
            raise ValueError('padding is not zero')
        command = struct.pack('<6I', LC_LOAD_DYLIB, size, 24, 2, 0x10000, 0x10000)+name
        data[end:end+size] = command+b'\0'*(size-len(command))
        struct.pack_into('<II', data, start+16, ncmds+1, sizeofcmds+size)
        done += 1
        print(f'x86_64 slice at {start:#x}: + LC_LOAD_DYLIB {path} (cmdsize {size}); padding left {first-32-sizeofcmds-size}')
    if done != 1:
        raise ValueError(f'expected one x86_64 slice, edited {done}')
    return bytes(data)


if __name__ == '__main__':
    source, target, dylib = sys.argv[1:4]
    with open(source, 'rb') as f:
        out = add(f.read(), dylib)
    with open(target, 'wb') as f:
        f.write(out)
