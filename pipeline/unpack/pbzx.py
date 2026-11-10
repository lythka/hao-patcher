#!/usr/bin/python3 -I
"""Decode one or more pbzx files to stdout (or -o out). Parallel per-file via multiprocessing; output in order."""
import sys, lzma, struct, os
from concurrent.futures import ProcessPoolExecutor

def decode(path):
    out = []
    with open(path, 'rb') as f:
        if f.read(4) != b'pbzx': raise SystemExit(f'{path}: not pbzx')
        f.read(8)  # flags / chunk size
        while True:
            hdr = f.read(16)
            if not hdr: break
            if len(hdr) < 16: raise SystemExit(f'{path}: truncated header')
            usize, csize = struct.unpack('>QQ', hdr)
            data = f.read(csize)
            if len(data) != csize: raise SystemExit(f'{path}: truncated chunk')
            if csize == usize:  # stored raw
                out.append(data)
            else:
                d = lzma.decompress(data, format=lzma.FORMAT_XZ)
                if len(d) != usize: raise SystemExit(f'{path}: size mismatch {len(d)} != {usize}')
                out.append(d)
    return b''.join(out)

if __name__ == '__main__':
    args = sys.argv[1:]
    outp = sys.stdout.buffer
    if args and args[0] == '-o':
        if os.path.exists(args[1]): raise SystemExit(f'{args[1]} exists; refusing to overwrite')
        outp = open(args[1], 'wb'); args = args[2:]
    with ProcessPoolExecutor(max_workers=min(12, os.cpu_count())) as ex:
        for buf in ex.map(decode, args):
            outp.write(buf)
    outp.flush()
