"""Code signatures of declared files: sign the way a spec says, read a signature back, and compare two files.

sign() runs macOS codesign once on a temporary copy, as the spec's "sign" says: an identifier or the input's own
(preserve), and, for "entitlements_from", the entitlements of another file, read with
`codesign -d --entitlements - --xml` exactly as the recipes' hooks do. Everything else here is pure Python.

read() parses each slice's embedded signature (LC_CODE_SIGNATURE superblob): the CodeDirectory's identifier, flags,
slot counts and CDHash, and the requirements, entitlements (XML) and DER entitlements blobs. compare() puts two
files side by side the way the acceptance check needs: code (every byte outside the signature, with the signature's
own size fields masked) and the signature's identifier, flags and entitlements. Padding the signer reserves after
the blobs (the builder VM's codesign adds about 18 KB of zeros per slice where the host's adds none) changes the
CDHash and the file size but nothing compare() reports as a difference: it is listed under "notes".
"""
import hashlib
import plistlib
import struct

LC_SEGMENT_64, LC_CODE_SIGNATURE = 0x19, 0x1d
CSMAGIC_EMBEDDED_SIGNATURE, CSMAGIC_CODEDIRECTORY = 0xfade0cc0, 0xfade0c02
SLOT_CODEDIRECTORY, SLOT_REQUIREMENTS, SLOT_ENTITLEMENTS, SLOT_DER_ENTITLEMENTS = 0, 2, 5, 7
CPU_NAMES = {0x1000007: 'x86_64', 0x100000c: 'arm64', 7: 'i386'}


class SignatureError(ValueError):
    pass


def slice_name(cpu, subtype):
    name = CPU_NAMES.get(cpu, hex(cpu))
    return name + 'e' if name == 'arm64' and subtype & 0xff == 2 else name + 'h' if name == 'x86_64' and \
        subtype & 0xff == 8 else name


def slices(data):
    """[(cpu, start, length, name)] of every Mach-O image in DATA (thin or universal); NAME tells arm64 from
    arm64e and x86_64 from x86_64h."""
    if data[:4] in (b'\xcf\xfa\xed\xfe', b'\xce\xfa\xed\xfe'):
        cpu, sub = struct.unpack_from('<II', data, 4)
        return [(cpu, 0, len(data), slice_name(cpu, sub))]
    if len(data) < 8:
        raise SignatureError('not a Mach-O')
    magic, count = struct.unpack_from('>II', data)
    if magic not in (0xcafebabe, 0xcafebabf) or not 0 < count <= 64:
        raise SignatureError('not a Mach-O or universal file')
    wide, out = magic == 0xcafebabf, []
    for i in range(count):
        at = 8 + i * (32 if wide else 20)
        cpu, sub = struct.unpack_from('>II', data, at)
        start, length = struct.unpack_from('>QQ' if wide else '>II', data, at + 8)
        if start + length > len(data):
            raise SignatureError('universal slice past the end of the file')
        out.append((cpu, start, length, slice_name(cpu, sub)))
    return out


def _commands(data, start):
    magic, cpu, _, _, ncmds, sizeofcmds = struct.unpack_from('<6I', data, start)
    at = start + (32 if magic == 0xfeedfacf else 28)
    for _ in range(ncmds):
        cmd, size = struct.unpack_from('<II', data, at)
        if size < 8:
            raise SignatureError('invalid load command')
        yield cmd, at, size
        at += size


def _signature_command(data, start):
    for cmd, at, size in _commands(data, start):
        if cmd == LC_CODE_SIGNATURE:
            return at
    return None


def _code_directory(blob):
    magic, length, version, flags, hash_offset, ident_offset, n_special, n_code = struct.unpack_from('>8I', blob)
    if magic != CSMAGIC_CODEDIRECTORY:
        raise SignatureError('CodeDirectory slot does not hold a CodeDirectory')
    hash_size, hash_type = blob[36], blob[37]
    ident = blob[ident_offset:blob.index(b'\0', ident_offset)].decode('utf-8', 'replace')
    digest = hashlib.sha256(blob[:length]).digest() if hash_type == 2 else hashlib.sha1(blob[:length]).digest()
    return {'identifier': ident, 'flags': flags, 'version': version, 'special_slots': n_special,
            'code_slots': n_code, 'hash_type': hash_type, 'hash_size': hash_size, 'cdhash': digest[:20].hex()}


def read(data):
    """{cpu name: signature} for every slice; signature is None for an unsigned slice, else identifier, flags,
    slot counts, cdhash, the requirements/entitlements/DER blobs (bytes or None), the signature's offset in the
    file, its declared size and how many of its bytes are trailing zeros after the last blob."""
    out = {}
    for cpu, start, length, name in slices(data):
        at = _signature_command(data, start)
        if at is None:
            out[name] = None
            continue
        dataoff, datasize = struct.unpack_from('<II', data, at + 8)
        sig = data[start + dataoff:start + dataoff + datasize]
        magic, total, count = struct.unpack_from('>III', sig)
        if magic != CSMAGIC_EMBEDDED_SIGNATURE:
            raise SignatureError('%s: LC_CODE_SIGNATURE does not point at an embedded signature' % name)
        blobs, end = {}, 12 + 8 * count
        for i in range(count):
            slot, off = struct.unpack_from('>II', sig, 12 + 8 * i)
            blen = struct.unpack_from('>I', sig, off + 4)[0]
            blobs.setdefault(slot, sig[off:off + blen])
            end = max(end, off + blen)
        if SLOT_CODEDIRECTORY not in blobs:
            raise SignatureError('%s: signature has no CodeDirectory' % name)
        cd = _code_directory(blobs[SLOT_CODEDIRECTORY])
        tail = sig[end:]
        cd.update(requirements=blobs.get(SLOT_REQUIREMENTS), entitlements=blobs.get(SLOT_ENTITLEMENTS),
                  der_entitlements=blobs.get(SLOT_DER_ENTITLEMENTS), offset=start + dataoff, size=datasize,
                  blob_bytes=end, padding=len(tail) - len(tail.rstrip(b'\0')), superblob_length=total)
        out[name] = cd
    return out


def outside(data):
    """{cpu name: bytes} of each slice without its signature: the signature region is cut, and the fields that only
    say how big it is (LC_CODE_SIGNATURE datasize, __LINKEDIT vmsize and filesize) are zeroed."""
    out = {}
    for cpu, start, length, name in slices(data):
        image = bytearray(data[start:start + length])
        at = _signature_command(data, start)
        cut = length
        if at is not None:
            dataoff = struct.unpack_from('<I', data, at + 8)[0]
            struct.pack_into('<I', image, at - start + 12, 0)
            cut = dataoff
            for cmd, cat, size in _commands(data, start):
                if cmd == LC_SEGMENT_64 and data[cat + 8:cat + 24].rstrip(b'\0') == b'__LINKEDIT':
                    struct.pack_into('<Q', image, cat - start + 32, 0)    # vmsize
                    struct.pack_into('<Q', image, cat - start + 48, 0)    # filesize
        out[name] = bytes(image[:cut])
    return out


def _entitlements(blob):
    """The entitlements dictionary of an entitlements blob (8-byte header, then an XML plist), or None."""
    if blob is None:
        return None
    try:
        return plistlib.loads(blob[8:])
    except Exception:            # noqa: BLE001 - a malformed blob compares by bytes
        return blob[8:]


def compare(reference, candidate):
    """{'problems': [...], 'notes': [...]} for CANDIDATE (made from declarations) against REFERENCE (what the
    recipe's own code installed). A problem: different code outside the signature, a slice signed in one and not
    the other, or a different identifier, CodeDirectory flags, entitlements, DER entitlements or requirements. A
    note: only the CDHash, the signature's size or its zero padding differ."""
    problems, notes = [], []
    a_code, b_code = outside(reference), outside(candidate)
    if sorted(a_code) != sorted(b_code):
        return {'problems': ['slices differ: %s vs %s' % (sorted(a_code), sorted(b_code))], 'notes': []}
    a_sig, b_sig = read(reference), read(candidate)
    for cpu in sorted(a_code):
        if a_code[cpu] != b_code[cpu]:
            first = next((i for i, (x, y) in enumerate(zip(a_code[cpu], b_code[cpu])) if x != y),
                         min(len(a_code[cpu]), len(b_code[cpu])))
            problems.append('%s: code differs outside the signature (first at slice offset %#x)' % (cpu, first))
        a, b = a_sig[cpu], b_sig[cpu]
        if (a is None) != (b is None):
            problems.append('%s: signed in %s only' % (cpu, 'the reference' if b is None else 'the candidate'))
            continue
        if a is None:
            continue
        for key in ('identifier', 'flags', 'hash_type', 'special_slots'):
            if a[key] != b[key]:
                problems.append('%s: signature %s %r, expected %r' % (cpu, key, b[key], a[key]))
        if _entitlements(a['entitlements']) != _entitlements(b['entitlements']):
            problems.append('%s: entitlements differ (%s vs %s expected)' % (
                cpu, 'none' if b['entitlements'] is None else '%d bytes' % len(b['entitlements']),
                'none' if a['entitlements'] is None else '%d bytes' % len(a['entitlements'])))
        if a['der_entitlements'] != b['der_entitlements']:
            problems.append('%s: DER entitlements differ' % cpu)
        if a['requirements'] != b['requirements']:
            problems.append('%s: requirements differ' % cpu)
        if a['cdhash'] != b['cdhash'] or a['size'] != b['size']:
            notes.append('%s: CDHash %s vs %s (signature size %d vs %d, zero padding %d vs %d)' % (
                cpu, b['cdhash'][:12], a['cdhash'][:12], b['size'], a['size'], b['padding'], a['padding']))
    return {'problems': problems, 'notes': notes}


def entitlements_xml(path):
    """The entitlements of the signed file at PATH as an XML plist (bytes), as the hooks read them; codesign
    prints nothing for a file without entitlements, which is refused (a recipe error, as in the hooks)."""
    import subprocess
    r = subprocess.run(['codesign', '-d', '--entitlements', '-', '--xml', str(path)], capture_output=True)
    if r.returncode or not r.stdout.strip():
        raise SignatureError('%s has no entitlements to take (%s)' % (path, r.stderr.decode(errors='replace').strip()[:200]))
    plistlib.loads(r.stdout)
    return r.stdout


def sign(data, sign_spec, original, entitlements_source=None, codesign_args=None):
    """DATA signed ad hoc as SIGN_SPEC says, once (codesign on a temporary copy). ORIGINAL (bytes) gives a
    preserved identifier; ENTITLEMENTS_SOURCE (bytes of the file entitlements_from names) gives the entitlements.
    CODESIGN_ARGS is spec.codesign_args (passed in: this module does not import its siblings)."""
    import subprocess
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as tmp:
        existing, ents = None, None
        if 'identifier' in sign_spec.get('preserve', []):
            Path(tmp, 'original').write_bytes(original)
            shown = subprocess.run(['codesign', '-dv', str(Path(tmp, 'original'))], capture_output=True, text=True)
            existing = next((l.split('=', 1)[1] for l in shown.stderr.splitlines() if l.startswith('Identifier=')), None)
        if sign_spec.get('entitlements_from'):
            if entitlements_source is None:
                raise SignatureError('sign takes its entitlements from another file, and it was not found')
            Path(tmp, 'entitlements-source').write_bytes(entitlements_source)
            ents = Path(tmp, 'entitlements.plist')
            ents.write_bytes(entitlements_xml(Path(tmp, 'entitlements-source')))
        Path(tmp, 'file').write_bytes(data)
        r = subprocess.run(codesign_args(sign_spec, existing, ents) + [str(Path(tmp, 'file'))], capture_output=True,
                           text=True)
        if r.returncode:
            raise SignatureError('codesign failed: %s' % r.stderr.strip()[:200])
        return Path(tmp, 'file').read_bytes()
