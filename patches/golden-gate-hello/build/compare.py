"""compare.py REFERENCE REBUILD: is REBUILD the same dylib as REFERENCE apart from the linker's stamp?

Prints "identical" when the files are byte-identical. Otherwise it copies REFERENCE's LC_UUID and the linker
version of LC_BUILD_VERSION's ld tool entry into a copy of REBUILD, re-signs that copy ad hoc with REFERENCE's
identifier, and prints "except ..." when the result is byte-identical to REFERENCE (exit 0), or "differ" (exit 1).
Run with `python3.14 -I`. Only thin x86_64 Mach-O files.
"""
import hashlib
import os
import pathlib
import struct
import subprocess
import sys
import tempfile

LC_UUID, LC_BUILD_VERSION, TOOL_LD = 0x1b, 0x32, 3


def stamps(data):
    """{'uuid': (offset, bytes), 'ld': (offset, bytes)} of a thin 64-bit Mach-O."""
    magic, _, _, _, ncmds, _, _, _ = struct.unpack_from('<IiiIIIII', data, 0)
    if magic != 0xfeedfacf:
        raise SystemExit('compare.py: not a thin 64-bit Mach-O')
    out, off = {}, 32
    for _ in range(ncmds):
        cmd, size = struct.unpack_from('<II', data, off)
        if cmd == LC_UUID:
            out['uuid'] = (off + 8, data[off + 8:off + 24])
        elif cmd == LC_BUILD_VERSION:
            ntools = struct.unpack_from('<I', data, off + 20)[0]
            for i in range(ntools):
                t = off + 24 + 8 * i
                if struct.unpack_from('<I', data, t)[0] == TOOL_LD:
                    out['ld'] = (t + 4, data[t + 4:t + 8])
        off += size
    return out


def identifier(path):
    r = subprocess.run(['codesign', '-dv', path], capture_output=True, text=True)
    for line in r.stderr.splitlines():
        if line.startswith('Identifier='):
            return line.split('=', 1)[1]
    raise SystemExit('compare.py: %s is not signed' % path)


def version(raw):
    v = struct.unpack('<I', raw)[0]
    return '%d.%d' % (v >> 16, (v >> 8) & 0xff)


def compare(reference, rebuild):
    """(True|False, description)"""
    ref, new = pathlib.Path(reference).read_bytes(), pathlib.Path(rebuild).read_bytes()
    if ref == new:
        return True, 'identical'
    rs, ns = stamps(ref), stamps(new)
    if len(ref) != len(new) or rs.keys() != ns.keys() or any(rs[k][0] != ns[k][0] for k in rs):
        return False, 'differ: size or load commands'
    fixed = bytearray(new)
    notes = []
    for k in sorted(rs):
        off, raw = rs[k]
        if raw != ns[k][1]:
            fixed[off:off + len(raw)] = raw
            notes.append('ld version %s vs %s' % (version(raw), version(ns[k][1])) if k == 'ld'
                         else 'UUID %s vs %s' % (raw.hex(), ns[k][1].hex()))
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, 'normalized.dylib')
        pathlib.Path(path).write_bytes(fixed)
        subprocess.run(['codesign', '-f', '-s', '-', '-i', identifier(reference), path], check=True,
                       capture_output=True)
        same = pathlib.Path(path).read_bytes() == ref
    if not same:
        return False, 'differ: not identical after taking the reference UUID and ld version'
    return True, 'except %s, and the ad hoc signature that follows (identical once they are equal, sha256 %s)' % (
        ', '.join(notes), hashlib.sha256(ref).hexdigest())


def main(argv):
    if len(argv) != 3:
        raise SystemExit(__doc__)
    ok, text = compare(argv[1], argv[2])
    print(text)
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv))
