#!/usr/bin/python3 -I
"""Minimal Apple Archive (YAA1) reader: yields (fields, blobs). Used to dump XAT blobs aa list doesn't show.
usage: yaa.py ARCHIVE [xat]  -> prints PAT and decoded xattrs for entries that carry XAT"""
import struct, sys
FIX = {'F': 4, 'G': 20, 'H': 32, 'I': 48, 'J': 64, 'T': 12, 'S': 8}
def entries(f):
    while True:
        h = f.read(6)
        if len(h) < 6: return
        assert h[:4] == b'YAA1', h
        hs, = struct.unpack('<H', h[4:]); body = f.read(hs - 6); i = 0; fields = {}; blobs = []
        while i < len(body):
            k = body[i:i+3].decode(); t = chr(body[i+3]); i += 4
            if t in '1248': n = int(t); fields[k] = int.from_bytes(body[i:i+n], 'little'); i += n
            elif t == 'P': n, = struct.unpack_from('<H', body, i); fields[k] = body[i+2:i+2+n]; i += 2 + n
            elif t in 'ABC': n = {'A': 2, 'B': 4, 'C': 8}[t]; sz = int.from_bytes(body[i:i+n], 'little'); i += n; blobs.append((k, sz))
            elif t in FIX: fields[k] = body[i:i+FIX[t]]; i += FIX[t]
            elif t == '*': fields[k] = True
            else: raise ValueError('field %s%s' % (k, t))
        data = {}
        for k, sz in blobs:
            if k in ('XAT', 'ACL', 'YEC', 'LNK', 'PAT'): data[k] = f.read(sz)
            else: f.seek(sz, 1); data[k] = sz
        yield fields, data
def xattrs(blob):
    out = {}; i = 0
    while i < len(blob):
        n, = struct.unpack_from('<I', blob, i); rec = blob[i+4:i+n]; nm, _, val = rec.partition(b'\0'); out[nm.decode()] = val; i += n
    return out
if __name__ == '__main__':
    with open(sys.argv[1], 'rb') as f:
        for fl, d in entries(f):
            if 'XAT' in d:
                print(fl.get('PAT', b'').decode('utf-8', 'surrogateescape'), {k: v[:80] for k, v in xattrs(d['XAT']).items()})
