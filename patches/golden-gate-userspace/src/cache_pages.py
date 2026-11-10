"""cache_pages.py: re-sign an x86_64 dyld shared cache file with 4 KiB code pages.

27.0's x86_64 shared cache ships in RosettaOS for Apple silicon and is signed
with 16 KiB CodeDirectory pages (2^14). The x86_64 kernel registers only 4 KiB
page signatures for it: dyld's F_ADDFILESIGS_RETURN fails with "code signature
registration for shared cache failed", while Tahoe's x86_64 caches carry
2^12 pages. The cache bytes are kept; the
signature is regenerated ad hoc in the same SHA-256 / version 0x20400 /
empty-requirements / empty-CMS shape as the original (and as
cache/shared-cache's dyld_rebuild/codesign.py), and the header's
codeSignatureSize is updated before hashing.
"""
import hashlib
import struct

PAGE = 4096
ALIGN = 16384
REQUIREMENTS = struct.pack(">III", 0xFADE0C01, 12, 0)


def _original(head, file_size, blob):
    """(limit, identifier, flags, execSegLimit, log2 page size) of the cache's CodeDirectory."""
    off, size = struct.unpack_from("<QQ", head, 0x28)
    if off == 0 or off + size != file_size or off % PAGE or len(blob) != size:
        raise ValueError("signature must be page aligned and end at EOF")
    magic, _, count = struct.unpack_from(">III", blob, 0)
    if magic != 0xFADE0CC0:
        raise ValueError("unknown SuperBlob")
    for k in range(count):
        typ, o = struct.unpack_from(">II", blob, 12 + 8 * k)
        if typ == 0:
            cd = blob[o:]
            (cmagic, _, version, flags, hoff, ioff, special, slots, limit,
             hsize, htype, plat, pgsz) = struct.unpack_from(">IIIIIIIIIBBBB", cd, 0)
            if cmagic != 0xFADE0C02 or (hsize, htype) != (32, 2) or version != 0x20400 or limit != off:
                raise ValueError("unsupported CodeDirectory")
            ident = cd[ioff:].split(b"\0", 1)[0]
            exec_limit = struct.unpack_from(">Q", cd, 72)[0]
            return off, ident, flags, exec_limit, pgsz
    raise ValueError("no CodeDirectory")


def resign_4k(data):
    """Return the cache file re-signed with 4 KiB pages (unchanged if already 4 KiB)."""
    off, size = struct.unpack_from("<QQ", data, 0x28)
    limit, ident, flags, exec_limit, pgsz = _original(data[:PAGE], len(data), data[off:off + size])
    if pgsz == 12:
        return bytes(data)
    ident += b"\0"
    hashes = 88 + len(ident) + 2 * 32
    cdsize = hashes + (limit // PAGE) * 32
    end = 36 + cdsize
    sigsize = (end + 20 + ALIGN - 1) // ALIGN * ALIGN
    code = bytearray(data[:limit])
    struct.pack_into("<Q", code, 0x30, sigsize)          # codeSignatureSize (inside page 0)
    sig = bytearray(sigsize)
    struct.pack_into(">9I", sig, 0, 0xFADE0CC0, end + 20, 3, 0, 36, 2, end, 0x10000, end + 12)
    struct.pack_into(">9I4B", sig, 36, 0xFADE0C02, cdsize, 0x20400, flags, hashes, 88, 2,
                     limit // PAGE, limit, 32, 2, 0, 12)
    struct.pack_into(">Q", sig, 36 + 72, exec_limit)
    sig[36 + 88:36 + 88 + len(ident)] = ident
    sig[36 + hashes - 64:36 + hashes - 32] = hashlib.sha256(REQUIREMENTS).digest()
    view = memoryview(code)
    for index, offset in enumerate(range(0, limit, PAGE)):
        start = 36 + hashes + index * 32
        sig[start:start + 32] = hashlib.sha256(view[offset:offset + PAGE]).digest()
    sig[end:end + 12] = REQUIREMENTS
    struct.pack_into(">II", sig, end + 12, 0xFADE0B01, 8)
    return bytes(code) + bytes(sig)


def resign_file(path, force=False):
    """Re-sign with bounded memory; force refreshes hashes after cache code edits."""
    with open(path, "r+b") as f:
        head = f.read(PAGE)
        off, size = struct.unpack_from("<QQ", head, 0x28)
        f.seek(off)
        tail = f.read(size)
        f.seek(0, 2)
        limit, ident, flags, exec_limit, pgsz = _original(head, f.tell(), tail)
        if pgsz == 12 and not force:
            return False
        ident += b"\0"
        hashes = 88 + len(ident) + 2 * 32
        cdsize = hashes + (limit // PAGE) * 32
        end = 36 + cdsize
        sigsize = (end + 20 + ALIGN - 1) // ALIGN * ALIGN
        page0 = bytearray(head)
        struct.pack_into("<Q", page0, 0x30, sigsize)
        sig = bytearray(sigsize)
        struct.pack_into(">9I", sig, 0, 0xFADE0CC0, end + 20, 3, 0, 36, 2, end, 0x10000, end + 12)
        struct.pack_into(">9I4B", sig, 36, 0xFADE0C02, cdsize, 0x20400, flags, hashes, 88, 2,
                         limit // PAGE, limit, 32, 2, 0, 12)
        struct.pack_into(">Q", sig, 36 + 72, exec_limit)
        sig[36 + 88:36 + 88 + len(ident)] = ident
        sig[36 + hashes - 64:36 + hashes - 32] = hashlib.sha256(REQUIREMENTS).digest()
        f.seek(0)
        index = 0
        while index * PAGE < limit:
            chunk = f.read(min(1 << 24, limit - index * PAGE))
            if index == 0:
                chunk = bytes(page0) + chunk[PAGE:]
            for k in range(0, len(chunk), PAGE):
                start = 36 + hashes + index * 32
                sig[start:start + 32] = hashlib.sha256(chunk[k:k + PAGE]).digest()
                index += 1
        sig[end:end + 12] = REQUIREMENTS
        struct.pack_into(">II", sig, end + 12, 0xFADE0B01, 8)
        f.seek(0)
        f.write(page0)
        f.seek(limit)
        f.write(sig)
        f.truncate()
    return True

