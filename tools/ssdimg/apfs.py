"""Read the APFS space-manager allocation bitmap of a container.

Used by `capture` so that only allocated blocks are copied off the SSD.
Structures follow Apple's "Apple File System Reference". Everything is
read-only. Any structure we don't understand raises ApfsError and the
caller falls back to copying the whole partition.

Safety argument: fsck_apfs cross-checks the space-manager bitmap against
every extent referenced by the file system, so on a container that passes
fsck the bitmap covers all live data. We additionally verify that the
number of set bits we decode equals the per-chunk and device free counts.
"""
import struct

NX_MAGIC = b"NXSB"
OBJ_TYPE_MASK = 0x0000FFFF
OBJECT_TYPE_NX_SUPERBLOCK = 0x01
OBJECT_TYPE_CHECKPOINT_MAP = 0x0C
OBJECT_TYPE_SPACEMAN = 0x05
NX_INCOMPAT_FUSION = 0x100
CI_COUNT_MASK = 0x000FFFFF


class ApfsError(Exception):
    pass


def fletcher64(block):
    words = struct.unpack_from(f"<{(len(block) - 8) // 4}I", block, 8)
    m = 0xFFFFFFFF
    s1 = s2 = 0
    for w in words:
        s1 = (s1 + w) % m
        s2 = (s2 + s1) % m
    c1 = m - ((s1 + s2) % m)
    c2 = m - ((s1 + c1) % m)
    return (c2 << 32) | c1


def _valid(block):
    return struct.unpack_from("<Q", block, 0)[0] == fletcher64(block)


def _hdr(block):
    oid, xid, otype, osub = struct.unpack_from("<QQII", block, 8)
    return oid, xid, otype & OBJ_TYPE_MASK, osub


class Container:
    def __init__(self, read, part_offset, part_size):
        """read(off, n) reads absolute disk bytes."""
        self._read = read
        self.base = part_offset
        b0 = read(part_offset, 4096)
        if b0[32:36] != NX_MAGIC:
            raise ApfsError("no NXSB magic at container start")
        self.bs = struct.unpack_from("<I", b0, 36)[0]
        if self.bs != 4096:
            b0 = read(part_offset, self.bs)
        self.sb = self._latest_superblock(b0)
        if self.block_count * self.bs > part_size:
            raise ApfsError("container larger than its partition")

    def block(self, paddr, count=1):
        if paddr < 0 or paddr + count > self.block_count:
            raise ApfsError(f"block address {paddr}+{count} is outside the container")
        return self._read(self.base + paddr * self.bs, count * self.bs)

    def _latest_superblock(self, b0):
        (self.block_count,) = struct.unpack_from("<Q", b0, 40)
        (incompat,) = struct.unpack_from("<Q", b0, 64)
        if incompat & NX_INCOMPAT_FUSION:
            raise ApfsError("Fusion containers are not supported")
        desc_blocks, data_blocks, desc_base, data_base = struct.unpack_from("<IIqq", b0, 104)
        if desc_blocks & 0x80000000 or data_blocks & 0x80000000:
            raise ApfsError("non-contiguous checkpoint areas are not supported")
        self.desc = (desc_base, desc_blocks)
        self.data = (data_base, data_blocks)
        best = None
        area = self.block(desc_base, desc_blocks)
        self.maps = {}
        for i in range(desc_blocks):
            blk = area[i * self.bs:(i + 1) * self.bs]
            if not _valid(blk):
                continue
            oid, xid, otype, _ = _hdr(blk)
            if otype == OBJECT_TYPE_NX_SUPERBLOCK and blk[32:36] == NX_MAGIC:
                if best is None or xid > best[0]:
                    best = (xid, blk)
            elif otype == OBJECT_TYPE_CHECKPOINT_MAP:
                self.maps.setdefault(xid, []).append(blk)
        if best is None:
            raise ApfsError("no valid superblock in checkpoint descriptor area")
        self.xid, sb = best
        (self.block_count,) = struct.unpack_from("<Q", sb, 40)
        (self.spaceman_oid,) = struct.unpack_from("<Q", sb, 152)
        return sb

    def _ephemeral(self, oid):
        for blk in self.maps.get(self.xid, []):
            (count,) = struct.unpack_from("<I", blk, 36)
            for k in range(count):
                otype, osub, size, _pad, fs_oid, m_oid, paddr = struct.unpack_from(
                    "<IIIIQQQ", blk, 40 + k * 40)
                if m_oid == oid:
                    n = (size + self.bs - 1) // self.bs
                    data_base, data_blocks = self.data
                    out = b""
                    for j in range(n):  # the data area is a ring buffer
                        p = data_base + (paddr - data_base + j) % data_blocks
                        out += self.block(p)
                    # The checksum covers the whole object, even if multi-block.
                    if not _valid(out[:size]):
                        raise ApfsError(f"ephemeral object {oid:#x} has a bad checksum")
                    return out[:size]
        raise ApfsError(f"ephemeral object {oid:#x} not in checkpoint map")

    def allocation(self):
        """Return (ranges, stats). ranges = sorted [(first_block, count)] of
        container blocks that must be copied."""
        sm = self._ephemeral(self.spaceman_oid)
        if _hdr(sm)[2] != OBJECT_TYPE_SPACEMAN:
            raise ApfsError("spaceman object has wrong type")
        sm_bs, blocks_per_chunk, chunks_per_cib, cibs_per_cab = struct.unpack_from("<IIII", sm, 32)
        (blk_count, chunk_count, cib_count, cab_count, free_count, addr_off) = struct.unpack_from(
            "<QQIIQI", sm, 48)
        (ip_block_count,) = struct.unpack_from("<Q", sm, 152)
        (ip_bm_block_count,) = struct.unpack_from("<I", sm, 164)
        ip_bm_base, ip_base = struct.unpack_from("<qq", sm, 168)
        if sm_bs != self.bs or blk_count != self.block_count:
            raise ApfsError("spaceman geometry does not match superblock")

        n_addr = cab_count if cab_count else cib_count
        addrs = struct.unpack_from(f"<{n_addr}Q", sm, addr_off)
        cibs = []
        if cab_count:
            for a in addrs:
                cab = self.block(a)
                if not _valid(cab):
                    raise ApfsError("bad CAB checksum")
                (cnt,) = struct.unpack_from("<I", cab, 36)
                cibs.extend(struct.unpack_from(f"<{cnt}Q", cab, 40))
        else:
            cibs = list(addrs)

        ranges = []
        bits_total = 0
        ci_free_total = 0
        chunks_seen = 0
        for a in cibs:
            cib = self.block(a)
            if not _valid(cib):
                raise ApfsError("bad CIB checksum")
            (cnt,) = struct.unpack_from("<I", cib, 36)
            for k in range(cnt):
                ci_xid, ci_addr, ci_bc, ci_fc, ci_bm = struct.unpack_from("<QQIIQ", cib, 40 + k * 32)
                ci_bc &= CI_COUNT_MASK
                ci_fc &= CI_COUNT_MASK
                chunks_seen += 1
                ci_free_total += ci_fc
                if ci_fc == ci_bc:
                    continue
                if ci_bm == 0:
                    # No bitmap but not fully free: be conservative.
                    ranges.append((ci_addr, ci_bc))
                    bits_total += ci_bc
                    continue
                bm = self.block(ci_bm)
                used = _bitmap_ranges(bm, ci_addr, ci_bc)
                n_used = sum(c for _, c in used)
                if n_used != ci_bc - ci_fc:
                    raise ApfsError(
                        f"chunk at {ci_addr}: bitmap has {n_used} used blocks, "
                        f"chunk info says {ci_bc - ci_fc}")
                bits_total += n_used
                ranges.extend(used)

        if chunks_seen != chunk_count:
            raise ApfsError(f"found {chunks_seen} chunk infos, expected {chunk_count}")
        if ci_free_total != free_count:
            raise ApfsError(f"chunk free counts sum to {ci_free_total}, device says {free_count}")

        # Always include fixed metadata regions (harmless if already marked).
        ranges.append((0, 1))
        ranges.append(self.desc)
        ranges.append(self.data)
        if ip_block_count:
            ranges.append((ip_base, ip_block_count))
        if ip_bm_block_count:
            ranges.append((ip_bm_base, ip_bm_block_count))
        merged = _merge(ranges)
        stats = {
            "block_size": self.bs,
            "block_count": self.block_count,
            "free_blocks": free_count,
            "used_blocks_bitmap": bits_total,
            "copy_blocks": sum(c for _, c in merged),
            "xid": self.xid,
        }
        return merged, stats


def _bitmap_ranges(bm, base, nbits):
    """Runs of set bits in a little-endian bitmap -> [(base+start, count)]."""
    out = []
    val = int.from_bytes(bm[:(nbits + 7) // 8], "little")
    if nbits % 8:
        val &= (1 << nbits) - 1
    pos = 0
    while val:
        tz = (val & -val).bit_length() - 1  # first set bit
        val >>= tz
        pos += tz
        inv = ~val
        run = (inv & -inv).bit_length() - 1  # length of the run of ones
        out.append((base + pos, run))
        val >>= run
        pos += run
    return out


def _merge(ranges):
    out = []
    for s, c in sorted(r for r in ranges if r[1] > 0):
        if out and s <= out[-1][0] + out[-1][1]:
            ps, pc = out[-1]
            out[-1] = (ps, max(pc, s + c - ps))
        else:
            out.append((s, c))
    return out
