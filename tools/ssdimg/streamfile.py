"""Image bundles: a commit written out as the exact frame stream `ssdimg push` sends, so the Recovery's Reinstall macOS
(patches/rec-reinstall: ssdinstall -> ssdpush-agent) can install it from a USB disk without this Mac.

    ssdimg bundle OUTDIR [--rev R] [--name NAME] [--model M] [--macos-version V] [--macos-build B] [--segment-size 2G]
    ssdimg bundle-check OUTDIR        re-read everything, re-hash every data chunk

On the USB disk the bundle is a folder at the top level of any volume (FAT32, exFAT, APFS or HFS+): ssdinstall
list-images finds /Volumes/*/*/IMAGE. Bundle layout (plain files, each < 4 GiB so FAT32 can hold them):
    IMAGE         key=value text: geometry, provenance, counts, sha256 of index.bin / head.bin / tail.bin
    index.bin     one 64-byte entry per frame, in stream order (ENTRY): where the frame is, what it writes, the content
                  hash. ssdinstall checks every frame header against it and uses it for the verify pass (agent 'Q')
                  and for --delta (frames sent out of order, by seeking).
    stream.NNN    the 'D' / 'Z' frames push sends, back to back, split only at frame boundaries
    head.bin      raw bytes of chunk 0 (MBR + primary GPT): ssdinstall relocates the backup GPT to the end of the
                  target itself and compares chunk 0 byte for byte afterwards
    tail.bin      raw bytes of a partial last chunk, if the image has one (compared byte for byte)

Integrity chain: IMAGE (not signed) -> sha256 of index.bin -> per-frame BLAKE2b-160 of each chunk (checked by the agent
before writing, and again on the disk by the verify pass) + offset/length/size of every frame header (checked by
ssdinstall against the index before it is forwarded).
The IMAGE keys are settable because pipeline/run images carry no probed facts.
"""
import hashlib
import os
import struct
import sys
import time

from .store import StoreError, chunk_hash, human

FORMAT = "ssdstream1"
ENTRY = struct.Struct("<cBHIQQII20s12x")   # type, 0, segment, 0, segment offset, disk offset, wlen, clen, hash
assert ENTRY.size == 64
D_HDR = struct.Struct("<QI20sI")             # after 'D': off, wlen, hash, clen
Z_HDR = struct.Struct("<QI")                 # after 'Z': off, wlen
SEGMENT = 2 << 30


def parse_size(s):
    m = {"K": 1 << 10, "M": 1 << 20, "G": 1 << 30}
    return int(float(s[:-1]) * m[s[-1].upper()]) if s[-1].upper() in m else int(s)


def export(repo, rev, out, name=None, segment=SEGMENT, keys=None):
    from .cli import _commit_manifest, keep_ranges, chunk_masks
    man, meta, label = _commit_manifest(repo, rev)
    size, cs, sector = meta["size"], meta["chunk_size"], meta["sector_size"]
    read = repo.reader(man, size)
    g, keep = keep_ranges(read, size, sector)
    present = sorted(set(man) | set(chunk_masks(keep, size, cs)))
    if (g.entries_lba + g.entries_sectors) * sector > cs:
        raise StoreError("primary GPT does not fit in chunk 0")
    nchunks = (size + cs - 1) // cs
    tail = nchunks - 1 if size % cs else None
    zero_hash = bytes.fromhex(chunk_hash(bytes(cs)))
    if os.path.exists(out):
        raise StoreError(f"{out} exists")
    tmp = out + ".partial"
    os.makedirs(tmp)
    idx = open(os.path.join(tmp, "index.bin"), "wb")
    seg_no, seg_f, seg_pos = -1, None, segment
    data_bytes = wire = 0
    t0 = time.time()
    for n, i in enumerate(present):
        off = i * cs
        wlen = min(cs, size - off)
        if i in man:
            h = man[i]
            with open(repo.obj_path(h), "rb") as f:
                blob = f.read()
            frame = b"D" + D_HDR.pack(off, wlen, bytes.fromhex(h), len(blob)) + blob
            kind, hb, clen = b"D", bytes.fromhex(h), len(blob)
        else:
            frame = b"Z" + Z_HDR.pack(off, wlen)
            kind, hb, clen = b"Z", zero_hash, 0
        if seg_pos + len(frame) > segment:
            if seg_f:
                seg_f.close()
            seg_no += 1
            seg_f, seg_pos = open(os.path.join(tmp, f"stream.{seg_no:03d}"), "wb"), 0
        idx.write(ENTRY.pack(kind, 0, seg_no, 0, seg_pos, off, wlen, clen, hb))
        seg_f.write(frame)
        seg_pos += len(frame)
        data_bytes += wlen
        wire += len(frame)
        if n % 4096 == 0:
            sys.stderr.write(f"\rbundle: {n * 100 // len(present):3d}% ({human(wire)})")
    sys.stderr.write(f"\rbundle: 100% ({human(wire)})\n")
    if seg_f:
        seg_f.close()
    idx.close()
    with open(os.path.join(tmp, "head.bin"), "wb") as f:
        f.write(read(0, cs))
    if tail is not None and tail in present:
        with open(os.path.join(tmp, "tail.bin"), "wb") as f:
            f.write(read(tail * cs, size - tail * cs))
    info = dict(meta.get("facts", {}), **{k: v for k, v in (keys or {}).items() if v})
    commit = repo.git("rev-parse", rev or "HEAD", capture=True).strip()
    kv = [("format", FORMAT), ("name", name or (rev or "HEAD")), ("rev", rev or "HEAD"), ("commit", commit),
          ("size", size), ("chunk_size", cs), ("sector_size", sector), ("frames", len(present)),
          ("data_frames", sum(1 for i in present if i in man)), ("data_bytes", data_bytes), ("stream_bytes", wire),
          ("segments", seg_no + 1), ("tail_index", tail if tail is not None and tail in present else -1),
          ("gpt_disk_guid", str(g.disk_guid).upper()),
          ("min_target_size", g.min_disk_sectors(align=1) * sector)]
    kv += [(k, info[k]) for k in ("model", "macos_version", "macos_build", "oclp_version") if k in info]
    kv.append(("created", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())))
    for fn in ("index.bin", "head.bin", "tail.bin"):
        p = os.path.join(tmp, fn)
        if os.path.exists(p):
            kv.append((f"sha256.{fn}", hashlib.sha256(open(p, "rb").read()).hexdigest()))
    with open(os.path.join(tmp, "IMAGE"), "w") as f:
        f.writelines(f"{k}={v}\n" for k, v in kv)
    os.rename(tmp, out)
    print(f"bundle {rev or 'HEAD'} ({commit[:7]}) -> {out}: {len(present)} frames, {human(data_bytes)} on disk, "
          f"{human(wire)} in {seg_no + 1} segment(s), {time.time() - t0:.0f}s")


def read_image(d):
    kv = dict(l.rstrip("\n").split("=", 1) for l in open(os.path.join(d, "IMAGE")) if "=" in l)
    if kv.get("format") != FORMAT:
        raise StoreError(f"{d}: not a {FORMAT} bundle")
    return kv


def check(d):
    """Re-read the bundle: hashes of index/head/tail, every frame header against its index entry, and the
    BLAKE2b-160 of every data chunk (decompressed)."""
    from compression import zstd
    kv = read_image(d)
    for k, v in kv.items():
        if k.startswith("sha256."):
            if hashlib.sha256(open(os.path.join(d, k[7:]), "rb").read()).hexdigest() != v:
                raise StoreError(f"{k[7:]}: sha256 mismatch")
    idx = open(os.path.join(d, "index.bin"), "rb").read()
    n = len(idx) // ENTRY.size
    if n != int(kv["frames"]):
        raise StoreError(f"index has {n} entries, IMAGE says {kv['frames']}")
    segs = {}
    for k in range(n):
        kind, _, seg, _, spos, off, wlen, clen, h = ENTRY.unpack_from(idx, k * ENTRY.size)
        f = segs.get(seg) or segs.setdefault(seg, open(os.path.join(d, f"stream.{seg:03d}"), "rb"))
        f.seek(spos)
        if f.read(1) != kind:
            raise StoreError(f"frame {k}: type differs from the index")
        if kind == b"D":
            o, w, hh, c = D_HDR.unpack(f.read(D_HDR.size))
            blob = f.read(c)
            if (o, w, hh, c) != (off, wlen, h, clen) or chunk_hash(zstd.decompress(blob)) != h.hex():
                raise StoreError(f"frame {k} (offset {off}): header or content differs from the index")
        else:
            if Z_HDR.unpack(f.read(Z_HDR.size)) != (off, wlen):
                raise StoreError(f"frame {k}: zero frame header differs from the index")
        if k % 4096 == 0:
            sys.stderr.write(f"\rcheck: {k * 100 // n:3d}%")
    sys.stderr.write("\rcheck: 100%\n")
    print(f"{d}: {n} frames OK ({kv['name']}, {human(int(kv['data_bytes']))})")
