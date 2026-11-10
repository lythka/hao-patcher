"""Pre-push check: does this image have what the target Mac needs to boot it? (read-only)

Each check is a failure that stops a real boot:
  - ESP without \\EFI\\BOOT\\BOOTx64.efi: after an NVRAM reset (or with no boot entry yet) the firmware boots macOS's
    boot.efi directly, without OpenCore, and shows the prohibitory sign on this unsupported Mac (a warning only:
    stock OCLP ESPs have none);
  - volumes named "Macintosh HD" instead of the user's NAME / "NAME - Data" (and the boot-picker label with them);
  - firmlinks: a System built from installer files has none unless set (fsctl APFSIOC_FIRMLINK_CTL), and boot then
    stops at tzinit;
  - no blessed root snapshot or no kernel collection: nothing bootable on the System volume.
And one that is not about booting: an item the patches removed that the image still references in Dock's default
layout, which shows as a "?" Dock tile (refcheck.py rule L1). The patch-set record checks (L2-L4) are not run: this
repository keeps no such records.
Uses only diskutil/hdiutil (via disk.attach) and fsctl through ctypes; never writes to the image.
"""
import ctypes
import os
import plistlib
import subprocess
import tempfile

from . import disk, refcheck

APFSIOC_FIRMLINK_CTL = 0xC4084A3C          # same call and struct as Apple's UpdateBrainLibrary (see pipeline/unpack/pristine-tools/fl.c)


class _FirmlinkCtl(ctypes.Structure):
    _fields_ = [("target", ctypes.c_char * 1024), ("op", ctypes.c_uint32), ("flags", ctypes.c_uint32)]


def firmlink_target(path):
    """The firmlink target of a System-volume directory ('' if none), via fsctl GET (op 1). Works as a normal user."""
    libc = ctypes.CDLL(None, use_errno=True)
    c = _FirmlinkCtl(op=1)
    if libc.fsctl(os.fsencode(path), ctypes.c_ulong(APFSIOC_FIRMLINK_CTL), ctypes.byref(c), 1):
        return ""
    return c.target.decode(errors="replace")


def _plist(*cmd):
    return plistlib.loads(subprocess.run(cmd, capture_output=True, check=True).stdout)


def _mount_ro(dev, where):
    os.makedirs(where, exist_ok=True)
    # Root checks inspect actual ownership. Host read-only inventory uses noowners;
    # ownership must then be covered by an exact captured root receipt.
    r = subprocess.run(["diskutil", "mount", "readOnly", "-mountOptions", ("nobrowse,owners" if os.geteuid()==0 else "nobrowse,noowners"), "-mountPoint", where, dev],
                       capture_output=True, text=True)
    return r.returncode == 0


def _unmount(where):
    subprocess.run(["diskutil", "unmount", where], capture_output=True)


def _container(phys_dev):
    """(container dict) of the APFS container whose physical store is phys_dev (e.g. /dev/disk20s2)."""
    store = os.path.basename(phys_dev)
    for c in _plist("diskutil", "apfs", "list", "-plist").get("Containers", []):
        if any(p.get("DeviceIdentifier") == store for p in c.get("PhysicalStores", [])):
            return c
    return None


def check_image(image, name="macOS"):
    """Attach `image` (raw) read-only and return a list of problems (empty = OK). Prints each check."""
    problems = []

    def check(ok, what):
        print(f"  {'ok  ' if ok else 'FAIL'} {what}")
        if not ok:
            problems.append(what)

    def warn(ok, what):
        print(f"  {'ok  ' if ok else 'warn'} {what}")

    whole, ents = disk.attach(image, mount=False, readonly=True)
    tmp = tempfile.mkdtemp(prefix="ssdimg-check-")
    mounted = []
    try:
        # ESP: OpenCore + the firmware fallback loader
        esp = disk.part_dev(ents, disk.EFI_GUID)
        check(bool(esp), "EFI system partition present")
        if esp and _mount_ro(esp, f"{tmp}/esp"):
            mounted.append(f"{tmp}/esp")
            e = f"{tmp}/esp/EFI"
            check(os.path.isfile(f"{e}/OC/OpenCore.efi"), "ESP: EFI/OC/OpenCore.efi")
            check(os.path.isfile(f"{e}/OC/config.plist"), "ESP: EFI/OC/config.plist")
            # stock OCLP ESPs have none; without it an NVRAM reset needs OpenCore picked by hand (warning only)
            warn(os.path.isfile(f"{e}/BOOT/BOOTx64.efi"),
                 "ESP: EFI/BOOT/BOOTx64.efi (firmware fallback to OpenCore after an NVRAM reset)")
        elif esp:
            check(False, "ESP mounts read-only")

        # APFS volumes by role
        apfs = disk.part_dev(ents, disk.APFS_GUID)
        c = _container(apfs) if apfs else None
        check(c is not None, "APFS container present")
        if c is None:
            return problems
        vols = {}
        for v in c.get("Volumes", []):
            for role in v.get("Roles", []):
                vols.setdefault(role, v)
        sysv, datav, pbv = vols.get("System"), vols.get("Data"), vols.get("Preboot")
        check(bool(sysv and datav and pbv), "System, Data and Preboot volumes present")
        if not (sysv and datav and pbv):
            return problems
        check(sysv["Name"] == name, f"System volume named {name!r} (is {sysv['Name']!r})")
        check(datav["Name"] == f"{name} - Data", f"Data volume named {name + ' - Data'!r} (is {datav['Name']!r})")
        group = datav["APFSVolumeUUID"]

        # Preboot: boot.efi and the boot-picker label for NAME
        if _mount_ro(pbv["DeviceIdentifier"], f"{tmp}/pb"):
            mounted.append(f"{tmp}/pb")
            cs = f"{tmp}/pb/{group}/System/Library/CoreServices"
            check(os.path.isfile(f"{cs}/boot.efi"), "Preboot: <group>/System/Library/CoreServices/boot.efi")
            check(os.path.isfile(f"{cs}/.disk_label") and os.path.isfile(f"{cs}/.disk_label_2x"),
                  "Preboot: boot-picker label (.disk_label, .disk_label_2x)")
            try:
                label = open(f"{cs}/.disk_label.contentDetails").read()
            except OSError:
                label = None
            check(label == name, f"Preboot: label text {name!r} (is {label!r})")
        else:
            check(False, "Preboot mounts read-only")

        # System: kernel collection and firmlinks (Data mounted too, so GET reports them stitched)
        if _mount_ro(datav["DeviceIdentifier"], f"{tmp}/data"):
            mounted.append(f"{tmp}/data")
            # a store left by the build host or VM may say "indexing disabled"
            check(not os.path.exists(f"{tmp}/data/.Spotlight-V100"), "Data: no Spotlight store from the build")
        if _mount_ro(sysv["DeviceIdentifier"], f"{tmp}/sys"):
            mounted.append(f"{tmp}/sys")
            s = f"{tmp}/sys"
            # read-only attach: listSnapshots needs the volume mounted (-69854 otherwise)
            snaps = subprocess.run(["diskutil", "apfs", "listSnapshots", sysv["DeviceIdentifier"]],
                                   capture_output=True, text=True).stdout
            check("Will root to" in snaps, "System has a blessed root snapshot")
            check(os.path.isfile(f"{s}/System/Library/KernelCollections/BootKernelExtensions.kc"),
                  "System: BootKernelExtensions.kc")
            try:
                listed = [l.split("\t")[0] for l in open(f"{s}/usr/share/firmlinks").read().splitlines() if l.strip()]
            except OSError:
                listed = []
            dirs = [p for p in listed if os.path.isdir(s + p)]   # /AppleInternal, /pkg are listed but not on the volume
            missing = [p for p in dirs if not firmlink_target(s + p)]
            check(bool(dirs) and not missing,
                  f"System: firmlinks set ({len(dirs) - len(missing)}/{len(dirs)}"
                  + (f"; missing {', '.join(missing[:4])}{'...' if len(missing) > 4 else ''})" if missing else ")"))
        else:
            check(False, "System mounts read-only")

        # removed items: every Dock default tile under /System exists (refcheck.py L1)
        if f"{tmp}/sys" in mounted and f"{tmp}/data" in mounted:
            refcheck.check(f"{tmp}/sys", f"{tmp}/data", check,
                           preboot=f"{tmp}/pb/{group}" if f"{tmp}/pb" in mounted else None, patchset=None)
        else:
            check(False, "removed-item references: System and Data must both mount read-only to check them")
    finally:
        for m in reversed(mounted):
            _unmount(m)
        disk.detach(whole)
    return problems
