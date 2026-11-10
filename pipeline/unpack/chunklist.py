#!/usr/bin/python3 -I
"""chunklist.py LIST FILE: verify FILE against an Apple .chunklist (CNKL, SHA-256 per chunk).
Exit 0 = OK, 1 = FAIL."""
import hashlib, struct, sys

if len(sys.argv) != 3:
    sys.exit("usage: chunklist.py LIST FILE")
c = open(sys.argv[1], "rb").read()
magic, hsz, ver, cm, sm, _, n, coff, soff = struct.unpack_from("<4sIBBBBQQQ", c, 0)
if magic != b"CNKL":
    sys.exit("chunklist.py: %s is not a chunklist" % sys.argv[1])
f = open(sys.argv[2], "rb"); bad = 0; total = 0
for i in range(n):
    sz, = struct.unpack_from("<I", c, coff + i * 36); h = c[coff + i * 36 + 4: coff + i * 36 + 36]
    d = f.read(sz); total += len(d)
    if hashlib.sha256(d).digest() != h:
        bad += 1
extra = len(f.read(1))
ok = not bad and not extra
print(f"chunks={n} bad={bad} bytes={total} trailing={extra}", "OK" if ok else "FAIL")
sys.exit(0 if ok else 1)
