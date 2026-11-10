"""Add the shim to the display-state dylib's x86_64 slice, preserving all other slices.

Adapted from golden-gate-userspace/src/macho.py's add_load_dylib.
The caller re-signs with the existing identifier and entitlements.
"""
from pathlib import Path
import struct
import sys

DYLIB = b'/usr/lib/windowserver_filter.dylib'

def add(data):
    if data[:4] == b'\xca\xfe\xba\xbe':
        offsets = [struct.unpack_from('>IIIII', data, 8+20*i)[2]
                   for i in range(struct.unpack_from('>I', data, 4)[0])]
    elif data[:4] == b'\xcf\xfa\xed\xfe':
        offsets = [0]
    else:
        raise ValueError('unsupported Mach-O format')
    for start in offsets:
        magic, cpu, _, kind, count, length = struct.unpack_from('<6I', data, start)
        if cpu != 0x1000007:
            continue
        if magic != 0xfeedfacf or kind != 6:
            raise ValueError('expected x86_64 dylib')
        at, first = start+32, len(data)-start
        for _ in range(count):
            cmd, size = struct.unpack_from('<II', data, at)
            if cmd in (0xc, 0x80000018):
                name = struct.unpack_from('<I', data, at+8)[0]
                if data[at+name:at+size].split(b'\0')[0] == DYLIB:
                    return False
            if cmd == 0x19:
                for i in range(struct.unpack_from('<I', data, at+64)[0]):
                    offset = struct.unpack_from('<I', data, at+72+i*80+48)[0]
                    if offset:
                        first = min(first, offset)
            at += size
        size = (24+len(DYLIB)+1+7)//8*8
        end = start+32+length
        if at != end or end+size > start+first or any(data[end:end+size]):
            raise ValueError('no zero load-command padding')
        data[end:end+size] = struct.pack('<6I', 0xc, size, 24, 2, 0x10000, 0x10000) + (DYLIB+b'\0').ljust(size-24, b'\0')
        struct.pack_into('<II', data, start+16, count+1, length+size)
        return True
    raise ValueError('no x86_64 slice')

if __name__ == '__main__':
    source, target = map(Path, sys.argv[1:])
    data = bytearray(source.read_bytes())
    add(data)
    target.write_bytes(data)
