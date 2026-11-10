"""macOS disk helpers: diskutil/hdiutil wrappers and raw device I/O."""
import os
import plistlib
import re
import subprocess
import time

# Buses an external SSD can hang off. Internal storage is PCI-Express / Apple
# Fabric / SATA; disk images report "Disk Image".
EXTERNAL_BUSES = {"USB", "Thunderbolt", "FireWire"}


class DiskError(Exception):
    pass


def normalize(dev):
    """disk7, rdisk7, /dev/rdisk7, /dev/disk7s2 -> /dev/disk7 (or /dev/disk7s2)."""
    d = dev.strip()
    for prefix in ("/dev/rdisk", "/dev/disk", "rdisk", "disk"):
        if d.startswith(prefix):
            d = "/dev/disk" + d[len(prefix):]
            break
    if not re.fullmatch(r"/dev/disk\d+(s\d+)*", d):
        raise DiskError(f"{dev!r} is not a /dev/diskN device")
    return d


def external_whole(dev):
    """Check that `dev` names a whole, physical, external disk that does not
    back the running system. Returns (normalized /dev/diskN, diskutil info)."""
    dev = normalize(dev)
    i = info(dev)
    whole = "/dev/" + i["ParentWholeDisk"]
    if not i.get("WholeDisk"):
        raise DiskError(f"{dev} is a partition; use the whole disk {whole}")
    if whole in boot_disks():
        raise DiskError(f"{dev} is the running system's disk")
    kind = i.get("VirtualOrPhysical", "?")
    if kind != "Physical":
        raise DiskError(f"{dev} is not a physical disk ({kind}; a disk image or APFS container?)")
    if i.get("Internal"):
        raise DiskError(f"{dev} is an internal disk; only external USB/Thunderbolt disks are allowed")
    bus = i.get("BusProtocol", "?")
    if bus not in EXTERNAL_BUSES:
        raise DiskError(f"{dev} is on the {bus} bus; only {'/'.join(sorted(EXTERNAL_BUSES))} disks are allowed")
    return dev, i


def summary(i):
    """One line describing a disk from its diskutil info."""
    ssd = i.get("SolidState")
    ssd = "SSD" if ssd else ("rotational" if ssd is False else "SSD/HDD unknown")
    gb = i["TotalSize"] / 1e9
    return (f"{i['DeviceIdentifier']}: {i.get('MediaName', '?')}  {gb:.1f} GB  {i.get('BusProtocol', '?')}  "
            f"{'internal' if i.get('Internal') else 'external'}  {i.get('VirtualOrPhysical', '?').lower()}  "
            f"{ssd}  {i.get('DeviceBlockSize', 512)}-byte sectors")


def run(*cmd, check=True):
    p = subprocess.run(cmd, capture_output=True, text=True)
    if check and p.returncode:
        raise DiskError(f"{' '.join(cmd)} failed:\n{p.stdout}{p.stderr}")
    return p.stdout


def plist(*cmd):
    p = subprocess.run(cmd, capture_output=True)
    if p.returncode:
        raise DiskError(f"{' '.join(cmd)} failed:\n{p.stdout.decode()}{p.stderr.decode()}")
    return plistlib.loads(p.stdout)


def info(dev):
    return plist("diskutil", "info", "-plist", dev)


def whole_disk(dev):
    return "/dev/" + info(dev)["ParentWholeDisk"]


def raw(dev):
    """/dev/diskN -> /dev/rdiskN (unbuffered, much faster)."""
    return dev.replace("/dev/disk", "/dev/rdisk") if dev.startswith("/dev/disk") else dev


def boot_disks():
    """Whole disks (physical stores) backing the running system."""
    root = info("/")
    out = {root["ParentWholeDisk"]}
    try:
        c = info(root["APFSContainerReference"])
        for s in c.get("APFSPhysicalStores", []):
            out.add(info(s["APFSPhysicalStore"])["ParentWholeDisk"])
    except (KeyError, DiskError):
        pass
    return {"/dev/" + d for d in out}


def mounted_under(whole):
    """Mount points of every volume on `whole`, including APFS volumes in
    containers whose physical store lives on it."""
    whole = whole.replace("/dev/", "")
    devs = {whole}
    lst = plist("diskutil", "list", "-plist", whole)
    for d in lst.get("AllDisksAndPartitions", []):
        for p in d.get("Partitions", []):
            devs.add(p["DeviceIdentifier"])
    try:
        apfs = plist("diskutil", "apfs", "list", "-plist")
        for c in apfs.get("Containers", []):
            stores = {s["DeviceIdentifier"] for s in c.get("PhysicalStores", [])}
            if stores & devs:
                devs.add(c["ContainerReference"])
                for v in c.get("Volumes", []):
                    devs.add(v["DeviceIdentifier"])
    except DiskError:
        pass
    mounts = []
    for line in run("mount").splitlines():
        dev = line.split(" on ")[0].replace("/dev/", "")
        # Exact match, or a snapshot/child of a known device (disk9s4s1).
        if any(dev == d or dev.startswith(d + "s") for d in devs):
            mounts.append(line.split(" on ")[1].split(" (")[0])
    return mounts


def unmount_disk(dev):
    run("diskutil", "unmountDisk", dev)


class Reader:
    """Aligned random-access reads from a device or file. A short read is an
    error, never silently truncated data. `size` clamps the aligned read so it
    never runs past the end of the device."""

    def __init__(self, path, align=4096, size=None):
        self.fd = os.open(path, os.O_RDONLY)
        self.align = align
        self.size = size

    def __call__(self, off, n):
        a = self.align
        start = off // a * a
        end = (off + n + a - 1) // a * a
        if self.size is not None:
            end = min(end, max(self.size, off + n))
        data = os.pread(self.fd, end - start, start)
        if len(data) < off - start + n:
            raise DiskError(f"short read: wanted {n} bytes at {off}, got {max(0, len(data) - (off - start))}")
        return data[off - start:off - start + n]

    def close(self):
        os.close(self.fd)


def write_all(fd, data, off):
    """pwrite until every byte is on the device; raise on a short or failed write."""
    view = memoryview(data)
    while len(view):
        n = os.pwrite(fd, view, off)
        if n <= 0:
            raise DiskError(f"write of {len(view)} bytes at {off} returned {n}")
        view = view[n:]
        off += n


def device_size(path):
    if path.startswith("/dev/"):
        i = info(path)
        return i["TotalSize"], i.get("DeviceBlockSize", 512)
    return os.path.getsize(path), 512


# ---------- disk images ----------

def attach(image, mount=True, readonly=False, owners=False):
    """Attach a raw image. Returns (whole_dev, {dev: info}) per partition/volume."""
    cmd = ["hdiutil", "attach", "-plist", "-imagekey", "diskimage-class=CRawDiskImage"]
    if not mount:
        cmd.append("-nomount")
    if readonly:
        cmd.append("-readonly")
    if owners:
        cmd += ["-owners", "on"]
    cmd.append(image)
    out = plist(*cmd)
    ents = out["system-entities"]
    return whole_of(ents), ents


EFI_GUID = "C12A7328-F81F-11D2-BA4B-00A0C93EC93B"
APFS_GUID = "7C3457EF-0000-11AA-AA11-00306543ECAC"
HFS_GUID = "48465300-0000-11AA-AA11-00306543ECAC"


def whole_of(ents):
    """The partition-scheme (whole disk) entry of an hdiutil entity list."""
    for e in ents:
        if e.get("content-hint") == "GUID_partition_scheme":
            return e["dev-entry"]
    return min((e["dev-entry"] for e in ents), key=lambda d: (len(d), d))


def part_dev(ents, type_guid):
    """dev-entry of the first partition with the given GPT type GUID."""
    for e in ents:
        if e.get("unmapped-content-hint", e.get("content-hint")) == type_guid:
            return e["dev-entry"]
    return None


def detach(dev, force=False):
    for attempt in range(5):
        p = subprocess.run(["hdiutil", "detach", dev] + (["-force"] if force or attempt >= 3 else []),
                           capture_output=True, text=True)
        if p.returncode == 0:
            return
        time.sleep(1.5)
    raise DiskError(f"could not detach {dev}: {p.stderr.strip()}")


def attached_device(image):
    """If `image` is currently attached, return its /dev/diskN."""
    image = os.path.realpath(image)
    try:
        out = plist("hdiutil", "info", "-plist")
    except DiskError:
        return None
    for img in out.get("images", []):
        if os.path.realpath(img.get("image-path", "")) == image:
            ents = img.get("system-entities", [])
            if ents:
                return whole_of(ents)
    return None


def apfs_volumes(container_store_dev):
    """Volumes of the APFS container whose physical store is `container_store_dev`."""
    store = container_store_dev.replace("/dev/", "")
    apfs = plist("diskutil", "apfs", "list", "-plist")
    for c in apfs.get("Containers", []):
        if store in {s["DeviceIdentifier"] for s in c.get("PhysicalStores", [])}:
            return c["ContainerReference"], c.get("Volumes", [])
    raise DiskError(f"no APFS container on {container_store_dev}")


def wait_for(pred, timeout=30, interval=0.5):
    end = time.time() + timeout
    while time.time() < end:
        try:
            if pred():
                return True
        except DiskError:
            pass
        time.sleep(interval)
    return False
