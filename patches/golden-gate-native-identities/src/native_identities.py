"""native-identities: which native program the image re-signed ad hoc stands for which Apple-signed original.

  native_identities.py scan SYSTEM --source ROOT [--source ROOT ...]            print the rows and what was skipped
  native_identities.py install SYSTEM --source ROOT [--source ROOT ...]         install originals, list, record

Every recipe that edits a native program re-signs it ad hoc, which loses the program's Apple signature, so a requirement
such as `anchor apple` fails for it. hao-runtime's libhao_codeid (carried by the image's x86_64 Security.framework,
golden-gate-userspace) answers such a failure from an Apple-signed reference named in /usr/lib/hao/native-identities
(hao-runtime include/hao/idlist.h):

    <observed cdhash>  <reference cdhash>  <arch>  <reference path relative to />

This walks the finished System for main executables (MH_EXECUTE) whose x86_64 slice is signed ad hoc and finds, at the
same path in a source root (the build's pristine 27.x System, the Tahoe native donors), the Apple-signed x86_64 slice it
was made from:

- the source's slice is not ad hoc and has the same signing identifier;
- it is the same program: at least 90% of the original's signed bytes after the load commands, compared in 64-byte
  pieces at the same file offsets, are unchanged (an inserted load command or a replaced Info.plist section moves
  nothing);
- installed under /usr/lib/hao/originals/<path> (root:wheel 0644, directories 0755), the copy passes
  `codesign --verify --arch x86_64 -R='anchor apple'`.

Programs listed in hao's stub-identities, and any program for which no such original exists (OCLP's own programs, for
example), get no row; `scan` says why. A program that is still Apple-signed needs no row and is not touched.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys

CPU_X86_64 = 0x01000007
LIST = 'usr/lib/hao/native-identities'
STUBS = 'usr/lib/hao/stub-identities'
ORIGINALS = 'usr/lib/hao/originals'
RECORD = 'System/Library/CoreServices/golden-gate-native-identities.json'
HASHES = {1: hashlib.sha1, 2: hashlib.sha256, 3: hashlib.sha256, 4: hashlib.sha384}
RANK = {1: 1, 3: 2, 2: 3, 4: 4}                       # the kernel's preference among CodeDirectories
CS_ADHOC = 0x2
PIECE = 64


def slices(data):
    """[(cputype, offset, size)] of a thin or fat Mach-O; [] if not Mach-O."""
    magic = data[:4]
    if magic in (b'\xcf\xfa\xed\xfe', b'\xce\xfa\xed\xfe'):
        return [(struct.unpack_from('<i', data, 4)[0], 0, len(data))]
    if magic in (b'\xca\xfe\xba\xbe', b'\xca\xfe\xba\xbf'):
        wide = magic == b'\xca\xfe\xba\xbf'
        count = struct.unpack_from('>I', data, 4)[0]
        if count > 32:
            return []
        out = []
        for k in range(count):
            if wide:
                cpu, _, offset, size = struct.unpack_from('>iiQQ', data, 8 + 32 * k)
            else:
                cpu, _, offset, size = struct.unpack_from('>iiII', data, 8 + 20 * k)
            out.append((cpu, offset, size))
        return out
    return []


def signature(data, offset, size):
    """dict(filetype, adhoc, cdhash, identifier, limit) of a 64-bit slice, or None if unsigned/unsupported."""
    piece = data[offset:offset + size]
    if len(piece) < 32 or piece[:4] != b'\xcf\xfa\xed\xfe':
        return None
    filetype, count = struct.unpack_from('<II', piece, 12)
    at, blob = 32, None
    for _ in range(count):
        command, length = struct.unpack_from('<II', piece, at)
        if command == 0x1d:                             # LC_CODE_SIGNATURE
            dataoff, datasize = struct.unpack_from('<II', piece, at + 8)
            blob = piece[dataoff:dataoff + datasize]
        if length < 8:
            return None
        at += length
    if not blob or blob[:4] != b'\xfa\xde\x0c\xc0':
        return None
    best, cms = None, False
    for k in range(struct.unpack_from('>I', blob, 8)[0]):
        kind, at = struct.unpack_from('>II', blob, 12 + 8 * k)
        magic, length = struct.unpack_from('>II', blob, at)
        if kind == 0x10000 and length > 8:
            cms = True
        if magic == 0xfade0c02:
            cd = blob[at:at + length]
            flags, _, ident_offset = struct.unpack_from('>III', cd, 12)
            limit = struct.unpack_from('>I', cd, 32)[0]
            hash_type = cd[37]
            if hash_type in RANK and (best is None or RANK[hash_type] > RANK[best[0]]):
                best = (hash_type, cd, flags, ident_offset, limit)
    if not best:
        return None
    hash_type, cd, flags, ident_offset, limit = best
    return dict(filetype=filetype, adhoc=bool(flags & CS_ADHOC) or not cms,
                cdhash=HASHES[hash_type](cd).hexdigest()[:40],
                identifier=cd[ident_offset:cd.index(b'\0', ident_offset)].decode(errors='replace'), limit=limit)


def x86_64(path):
    """(slice signature, slice bytes) of the file's x86_64 slice, or (None, None)."""
    try:
        with open(path, 'rb') as stream:
            if stream.read(4) not in (b'\xcf\xfa\xed\xfe', b'\xca\xfe\xba\xbe', b'\xca\xfe\xba\xbf'):
                return None, None
            stream.seek(0)
            data = stream.read()
    except OSError:
        return None, None
    for cpu, offset, size in slices(data):
        if cpu == CPU_X86_64:
            return signature(data, offset, size), data[offset:offset + size]
    return None, None


def same_program(observed, reference, limit):
    """At least 90% of the reference's signed bytes after the load commands, compared in 64-byte pieces at the same
    offsets, are unchanged. The Mach-O header and load commands are left out: re-signing rewrites them, and an
    inserted load command takes the padding after them."""
    start = 32 + max(struct.unpack_from('<I', observed, 20)[0], struct.unpack_from('<I', reference, 20)[0])
    start = (start + PIECE - 1) // PIECE * PIECE
    pieces = max(1, (limit - start + PIECE - 1) // PIECE)
    same = sum(1 for at in range(start, limit, PIECE)
               if observed[at:min(at + PIECE, limit)] == reference[at:min(at + PIECE, limit)])
    return same * 10 >= pieces * 9, f'{same}/{pieces}'


def listed_stubs(system):
    path = Path(system)/STUBS
    if not path.is_file():
        return set()
    return {line.split()[0].lower() for line in path.read_text().splitlines()
            if line.strip() and not line.startswith('#')}


def candidates(system):
    """Relative paths of executables in SYSTEM whose x86_64 slice is an ad hoc signed MH_EXECUTE."""
    system = Path(system)
    skip = {system/ORIGINALS}
    for folder, dirs, names in os.walk(system):
        here = Path(folder)
        dirs[:] = sorted(d for d in dirs if here/d not in skip and not (here/d).is_symlink())
        for name in sorted(names):
            path = here/name
            try:
                info = path.lstat()
            except OSError:
                continue
            if not (info.st_mode & 0o111) or not path.is_file() or path.is_symlink() or info.st_size < 4096:
                continue
            sig, body = x86_64(path)
            if sig and sig['filetype'] == 2 and sig['adhoc']:
                yield path.relative_to(system).as_posix(), sig, body


def scan(system, sources):
    """(rows, skipped): rows = [dict(path, observed, reference, source, same)], skipped = {path: reason}."""
    stubs = listed_stubs(system)
    rows, skipped = [], {}
    for rel, sig, body in candidates(system):
        if sig['cdhash'] in stubs:
            skipped[rel] = 'hao stub (stub-identities)'
            continue
        reasons = []
        for source in sources:
            original = Path(source)/rel
            if not original.is_file() or original.is_symlink():
                continue
            ref, ref_body = x86_64(original)
            if not ref:
                reasons.append(f'{source}: no signed x86_64 slice')
            elif ref['adhoc']:
                reasons.append(f'{source}: not Apple-signed')
            elif ref['identifier'] != sig['identifier']:
                reasons.append(f'{source}: identifier {ref["identifier"]} != {sig["identifier"]}')
            elif ref['cdhash'] == sig['cdhash']:
                reasons.append(f'{source}: same cdhash')
            else:
                same, measure = same_program(body, ref_body, ref['limit'])
                if not same:
                    reasons.append(f'{source}: another program ({measure} code blocks unchanged)')
                    continue
                rows.append(dict(path=rel, observed=sig['cdhash'], reference=ref['cdhash'], source=str(original),
                                 identifier=sig['identifier'], same=measure))
                break
        else:
            skipped[rel] = '; '.join(reasons) or 'no original at this path in any source'
    return rows, skipped


def verify(path):
    result = subprocess.run(['codesign', '--verify', '--arch', 'x86_64', '-R=anchor apple', str(path)],
                            capture_output=True, text=True)
    return result.returncode == 0, result.stderr.strip()


def install(system, sources, owner=(0, 0)):
    """Copy each row's original under usr/lib/hao/originals, check it, write the list and a record."""
    system = Path(system)
    rows, skipped = scan(system, sources)
    hao = system/'usr/lib/hao'
    if not hao.is_dir():
        hao.mkdir(mode=0o755, parents=True)
        os.chown(hao, *owner)
    kept = []
    for row in rows:
        copy = system/ORIGINALS/row['path']
        made = []
        folder = copy.parent
        while not folder.exists():
            made.append(folder)
            folder = folder.parent
        for folder in reversed(made):
            folder.mkdir(mode=0o755)
            os.chown(folder, *owner)
        shutil.copyfile(row['source'], copy)
        os.chown(copy, *owner)
        os.chmod(copy, 0o644)
        ok, why = verify(copy)
        if not ok:
            copy.unlink()
            skipped[row['path']] = 'original fails anchor apple: ' + why
            continue
        kept.append(row)
    text = ['# native-identities: native programs this image re-signed ad hoc -> their Apple-signed originals',
            '# (golden-gate-native-identities; hao-runtime include/hao/idlist.h). Generated; do not edit.']
    text += [f'{r["observed"]}  {r["reference"]}  x86_64  {ORIGINALS}/{r["path"]}' for r in kept]
    target = system/LIST
    target.write_text('\n'.join(text) + '\n')
    os.chown(target, *owner)
    os.chmod(target, 0o644)
    record = dict(list=LIST, rows=kept, skipped=skipped, sources=[str(s) for s in sources],
                  list_sha256=hashlib.sha256(target.read_bytes()).hexdigest())
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('command', choices=('scan', 'install'))
    parser.add_argument('system', type=Path)
    parser.add_argument('--source', type=Path, action='append', required=True)
    parser.add_argument('--record', type=Path, help='where install writes its record (default: SYSTEM/' + RECORD + ')')
    args = parser.parse_args()
    if args.command == 'scan':
        rows, skipped = scan(args.system, args.source)
        print(json.dumps(dict(rows=rows, skipped=skipped), indent=2))
        return
    record = install(args.system, args.source)
    path = args.record or args.system/RECORD
    path.write_text(json.dumps(record, indent=2) + '\n')
    os.chmod(path, 0o644)
    for row in record['rows']:
        print('native identity:', row['path'], row['observed'], '->', row['reference'], f'({row["same"]} unchanged)',
              flush=True)
    print(f'native-identities: {len(record["rows"])} rows, {len(record["skipped"])} ad hoc programs without one',
          flush=True)


if __name__ == '__main__':
    sys.exit(main())
