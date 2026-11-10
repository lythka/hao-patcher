#!/usr/bin/python3 -I
"""chunklist.py: write or check a BaseSystem.chunklist (Apple's CNKL format, version 1: SHA-256 per 10 MiB chunk).

  chunklist.py create DMG OUT       unsigned chunklist for DMG (signature method 0, no signature)
  chunklist.py verify DMG CHUNKLIST every chunk hash and the total size; exit 1 on a mismatch

A Recovery we build has its own BaseSystem.dmg, so Apple's RSA-signed chunklist no longer matches it. OpenCore checks
a chunklist's chunk hashes when it loads a Recovery dmg and checks its signature only with DmgLoading Signed (the
image's OpenCore runs with Any, patches/opencore-config), so the Recovery volume gets this unsigned one. Format:
xnu bsd/kern/chunklist.h: header 0x24 bytes (magic CNKL, header size, file version 1, chunk method 1, signature
method, padding, chunk count, chunk offset, signature offset), then {uint32 size, sha256} per chunk. The chunk table
of `create` on Apple's own BaseSystem.dmg equals Apple's chunklist (pipeline/tests/test_chunklist.py).
"""
import hashlib, struct, sys

MAGIC, HDR, CHUNK = 0x4C4B4E43, 0x24, 10 << 20
HEAD = struct.Struct('<IIBBBBQQQ')
ENTRY = struct.Struct('<I32s')


def chunks(dmg):
    with open(dmg, 'rb') as f:
        for b in iter(lambda: f.read(CHUNK), b''):
            yield len(b), hashlib.sha256(b).digest()


def create(dmg, out):
    table = list(chunks(dmg))
    if not table:
        sys.exit('chunklist: %s is empty' % dmg)
    with open(out, 'wb') as f:
        f.write(HEAD.pack(MAGIC, HDR, 1, 1, 0, 0, len(table), HDR, 0))
        for e in table:
            f.write(ENTRY.pack(*e))
    print('%s: %d chunks, unsigned' % (out, len(table)))


def table(chunklist):
    b = open(chunklist, 'rb').read()
    magic, size, ver, method, sig, _, n, off, _ = HEAD.unpack_from(b)
    if (magic, size, ver, method) != (MAGIC, HDR, 1, 1) or off + n * ENTRY.size > len(b):
        sys.exit('chunklist: %s is not a version 1 chunklist' % chunklist)
    return [ENTRY.unpack_from(b, off + i * ENTRY.size) for i in range(n)], sig


def verify(dmg, chunklist):
    want, sig = table(chunklist)
    got = list(chunks(dmg))
    bad = sum(1 for a, b in zip(want, got) if a != b) + abs(len(want) - len(got))
    print('%s: %d chunks, %d bad, signature method %d' % (dmg, len(want), bad, sig))
    return 1 if bad else 0


if __name__ == '__main__':
    if len(sys.argv) != 4 or sys.argv[1] not in ('create', 'verify'):
        sys.exit(__doc__)
    sys.exit(create(*sys.argv[2:]) if sys.argv[1] == 'create' else verify(*sys.argv[2:]))
