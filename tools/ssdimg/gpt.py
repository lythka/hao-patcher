"""Minimal GUID Partition Table reader/writer.

Only what ssdimg needs: parse the primary GPT, list partitions, and
relocate the backup GPT to the end of a disk of a different size (used
when shrinking an image or writing it to a larger SSD).
"""
import struct
import uuid
import zlib

SECTOR = 512
HDR_FMT = "<8sIIIIQQQQ16sQIII"
HDR_SIZE = struct.calcsize(HDR_FMT)  # 92

TYPE_EFI = uuid.UUID("C12A7328-F81F-11D2-BA4B-00A0C93EC93B")
TYPE_APFS = uuid.UUID("7C3457EF-0000-11AA-AA11-00306543ECAC")
TYPE_HFS = uuid.UUID("48465300-0000-11AA-AA11-00306543ECAC")
TYPE_NAMES = {TYPE_EFI: "EFI", TYPE_APFS: "APFS", TYPE_HFS: "HFS+"}


class GPTError(Exception):
    pass


class Partition:
    def __init__(self, index, raw):
        t, u, self.first_lba, self.last_lba, self.attrs, name = struct.unpack("<16s16sQQQ72s", raw[:128])
        self.index = index
        self.type = uuid.UUID(bytes_le=t)
        self.guid = uuid.UUID(bytes_le=u)
        self.name = name.decode("utf-16-le").rstrip("\0")

    @property
    def type_name(self):
        return TYPE_NAMES.get(self.type, str(self.type))

    def offset(self, sector=SECTOR):
        return self.first_lba * sector

    def size(self, sector=SECTOR):
        return (self.last_lba - self.first_lba + 1) * sector


class GPT:
    """Parsed primary GPT. `read(off, n)` is any callable returning bytes."""

    def __init__(self, read, sector=SECTOR):
        self.sector = sector
        hdr = read(sector, sector)
        f = list(struct.unpack(HDR_FMT, hdr[:HDR_SIZE]))
        if f[0] != b"EFI PART":
            raise GPTError("no GPT signature at LBA 1")
        (self.sig, self.rev, self.hdr_size, crc, _r, self.my_lba, self.alt_lba,
         self.first_usable, self.last_usable, guid, self.entries_lba,
         self.num_entries, self.entry_size, self.entries_crc) = f
        chk = bytearray(hdr[:self.hdr_size])
        chk[16:20] = b"\0\0\0\0"
        if zlib.crc32(chk) != crc:
            raise GPTError("primary GPT header CRC mismatch")
        self.disk_guid = uuid.UUID(bytes_le=guid)
        n = self.num_entries * self.entry_size
        self.entries_raw = read(self.entries_lba * sector, _round_up(n, sector))[:n]
        if zlib.crc32(self.entries_raw) != self.entries_crc:
            raise GPTError("GPT partition entry array CRC mismatch")
        self.mbr = read(0, sector)
        self.partitions = []
        for i in range(self.num_entries):
            raw = self.entries_raw[i * self.entry_size:(i + 1) * self.entry_size]
            if raw[:16] != b"\0" * 16:
                self.partitions.append(Partition(i + 1, raw))

    @property
    def entries_sectors(self):
        return _round_up(self.num_entries * self.entry_size, self.sector) // self.sector

    def end_lba(self):
        """One past the last LBA used by any partition."""
        return max(p.last_lba for p in self.partitions) + 1

    def min_disk_sectors(self, align=2048):
        """Smallest disk (in sectors) that holds every partition plus a backup GPT."""
        return _round_up(self.end_lba() + self.entries_sectors + 1, align)

    def apfs_partitions(self):
        return [p for p in self.partitions if p.type == TYPE_APFS]

    def describe(self):
        lines = [f"disk_guid {self.disk_guid}", f"sector_size {self.sector}"]
        for p in self.partitions:
            lines.append(
                f"part {p.index} {p.type_name} lba {p.first_lba}-{p.last_lba} "
                f"size {p.size(self.sector)} guid {p.guid} name {p.name!r}")
        return "\n".join(lines) + "\n"

    def relocated(self, total_sectors):
        """Return [(offset, bytes)] writes that make this GPT valid for a disk
        of `total_sectors`: backup header at the last LBA, backup entries just
        before it, updated last-usable LBA and protective MBR."""
        s = self.sector
        last = total_sectors - 1
        b_entries = last - self.entries_sectors
        last_usable = b_entries - 1
        if self.end_lba() - 1 > last_usable:
            raise GPTError(
                f"partitions end at LBA {self.end_lba() - 1} but disk of "
                f"{total_sectors} sectors only allows up to {last_usable}")
        entries = self.entries_raw.ljust(self.entries_sectors * s, b"\0")
        primary = self._header(1, last, self.entries_lba, last_usable)
        backup = self._header(last, 1, b_entries, last_usable)
        return [
            (0, self._mbr(total_sectors)),
            (s, primary.ljust(s, b"\0")),
            (self.entries_lba * s, entries),
            (b_entries * s, entries),
            (last * s, backup.ljust(s, b"\0")),
        ]

    def _header(self, my, alt, entries_lba, last_usable):
        f = [self.sig, self.rev, self.hdr_size, 0, 0, my, alt, self.first_usable,
             last_usable, self.disk_guid.bytes_le, entries_lba, self.num_entries,
             self.entry_size, self.entries_crc]
        h = bytearray(struct.pack(HDR_FMT, *f)).ljust(self.hdr_size, b"\0")
        h[16:20] = struct.pack("<I", zlib.crc32(h))
        return bytes(h)

    def _mbr(self, total_sectors):
        m = bytearray(self.mbr)
        # Protective MBR: first entry type 0xEE spanning the disk (capped).
        if m[446 + 4] == 0xEE:
            struct.pack_into("<I", m, 446 + 12, min(total_sectors - 1, 0xFFFFFFFF))
        return bytes(m)


def _round_up(n, a):
    return (n + a - 1) // a * a
