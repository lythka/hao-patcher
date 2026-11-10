#!/usr/bin/python3 -I
"""bx50full.py PATCH OUT: decode a full-replacement BXDIFF50 patch (0 entries, 0-byte control: the body is one pbzx
stream that is the whole output). Checks the output size and the SHA-1 stored at header offset 0x28.
Exit 0 = MATCH, 1 = size or SHA-1 mismatch, 2 = not a full-replacement BXDIFF50. Refuses to overwrite OUT."""
import hashlib, lzma, os, struct, sys

if len(sys.argv) != 3:
    sys.exit("usage: bx50full.py PATCH OUT")
p, o = sys.argv[1], sys.argv[2]
if os.path.exists(o):
    sys.exit("bx50full.py: %s exists; refusing to overwrite" % o)
f = open(p, "rb"); h = f.read(0x3c)
if h[:8] != b"BXDIFF50":
    print("not a BXDIFF50 patch", file=sys.stderr); sys.exit(2)
n, outsz, asz, bsz = struct.unpack_from("<QQQQ", h, 8)
if n != 0 or asz != 0:
    print("not a full-replacement patch (entries %d, control %d)" % (n, asz), file=sys.stderr); sys.exit(2)
if f.read(4) != b"pbzx":
    print("body is not pbzx", file=sys.stderr); sys.exit(2)
f.read(8)
s1 = hashlib.sha1(); total = 0
with open(o, "wb") as out:
    while True:
        hd = f.read(16)
        if len(hd) < 16:
            break
        u, c = struct.unpack(">QQ", hd); d = f.read(c)
        d = d if c == u else lzma.decompress(d)
        out.write(d); s1.update(d); total += len(d)
ok = s1.digest() == h[0x28:0x3c] and total == outsz
print("size", total, "expected", outsz, "sha1", s1.hexdigest(), "header", h[0x28:0x3c].hex(), "MATCH" if ok else "NO-MATCH")
sys.exit(0 if ok else 1)
