"""Identify what a macOS / OCLP drive holds: Mac model, macOS version, OCLP version.

Everything is read from plists on the drive's own volumes:
  model         Library/Preferences/SystemConfiguration/preferences.plist   (Data volume)
                fallback: #Revision in EFI/OC/config.plist                   (EFI partition)
  macOS         System/Library/CoreServices/SystemVersion.plist             (System volume)
  OCLP          System/Library/CoreServices/OpenCore-Legacy-Patcher.plist   (System volume,
                written by the root patcher; its Commit URL carries the exact release tag)
                fallback: the OpenCore-Patcher.app bundle version          (Data volume)
"""
import os
import plistlib
import re
import subprocess

from . import disk

MACOS_NAMES = {
    "10.13": "highsierra", "10.14": "mojave", "10.15": "catalina",
    "11": "bigsur", "12": "monterey", "13": "ventura", "14": "sonoma", "15": "sequoia", "26": "tahoe",
}


def _plist(path):
    try:
        with open(path, "rb") as fh:
            return plistlib.load(fh)
    except (OSError, plistlib.InvalidFileException, ValueError):
        return {}


def macos_name(version):
    parts = version.split(".")
    key = ".".join(parts[:2]) if parts[0] == "10" else parts[0]
    return MACOS_NAMES.get(key, "macos")


def oclp_short(tag):
    """'3.0.0-rc.3' -> '3.0.0rc3', 'v3.0.0' -> '3.0.0'."""
    head, *rest = tag.strip().strip("/").lstrip("vV").split("-")
    return head + "".join(p.replace(".", "") for p in rest)


def probe(whole):
    """Facts about the drive `whole` (/dev/diskN). Volumes that are not mounted
    are mounted read-only for the duration when running as root."""
    facts = {}
    mounted = []

    def mount_ro(dev):
        mp = disk.info(dev).get("MountPoint")
        if mp or os.geteuid() != 0:
            return mp
        r = subprocess.run(["diskutil", "mount", "readOnly", dev], capture_output=True, text=True)
        if r.returncode:
            return None
        mounted.append(dev)
        return disk.info(dev).get("MountPoint")

    try:
        lst = disk.plist("diskutil", "list", "-plist", whole.replace("/dev/", ""))
        parts = [p for d in lst.get("AllDisksAndPartitions", []) for p in d.get("Partitions", [])]
        apfs_part = next((p["DeviceIdentifier"] for p in parts if p.get("Content") == "Apple_APFS"), None)
        efi_part = next((p["DeviceIdentifier"] for p in parts if p.get("Content") == "EFI"), None)
        roles = {}
        if apfs_part:
            try:
                _ref, volumes = disk.apfs_volumes("/dev/" + apfs_part)
                for v in volumes:
                    for role in v.get("Roles", []):
                        roles.setdefault(role, "/dev/" + v["DeviceIdentifier"])
            except disk.DiskError:
                pass
        sysmp = mount_ro(roles["System"]) if "System" in roles else None
        datamp = mount_ro(roles["Data"]) if "Data" in roles else None
        if sysmp:
            sv = _plist(os.path.join(sysmp, "System/Library/CoreServices/SystemVersion.plist"))
            if sv.get("ProductVersion"):
                facts["macos_version"] = sv["ProductVersion"]
                facts["macos_build"] = sv.get("ProductBuildVersion", "")
            rp = _plist(os.path.join(sysmp, "System/Library/CoreServices/OpenCore-Legacy-Patcher.plist"))
            url = rp.get("Commit URL", "")
            if "/tag/" in url:
                facts["oclp_version"] = url.rsplit("/tag/", 1)[1].strip("/")
            elif rp.get("OpenCore Legacy Patcher"):
                facts["oclp_version"] = str(rp["OpenCore Legacy Patcher"]).lstrip("vV")
            if rp.get("Time Patched"):
                facts["oclp_patched"] = rp["Time Patched"]
        if datamp:
            for rel in ("Library/Preferences/SystemConfiguration/preferences.plist",
                        "Library/Preferences/SystemConfiguration/NetworkInterfaces.plist"):
                model = _plist(os.path.join(datamp, rel)).get("Model")
                if model:
                    facts["model"] = model
                    break
            if "oclp_version" not in facts:
                info = _plist(os.path.join(datamp, "Library/Application Support/Dortania/OpenCore-Patcher.app/"
                                                   "Contents/Info.plist"))
                if info.get("CFBundleShortVersionString"):
                    facts["oclp_version"] = info["CFBundleShortVersionString"]
        if "model" not in facts and efi_part:
            efimp = mount_ro("/dev/" + efi_part)
            if efimp:
                rev = _plist(os.path.join(efimp, "EFI/OC/config.plist")).get("#Revision", {})
                for k, v in rev.items():
                    if "model" in k.lower() and isinstance(v, str) and v:
                        facts["model"] = v
                        break
    finally:
        for dev in reversed(mounted):
            subprocess.run(["diskutil", "unmount", dev], capture_output=True)
    if "macos_version" in facts:
        facts["macos_name"] = macos_name(facts["macos_version"])
    return facts


def repo_name(facts, fallback):
    """macbookpro9,2-tahoe_v26.7.1-oclp_v3.0.0rc3 (model, macOS, OCLP), or `fallback`."""
    model, ver = facts.get("model"), facts.get("macos_version")
    if not (model and ver):
        return fallback
    parts = [model.lower(), f"{facts.get('macos_name', 'macos')}_v{ver}"]
    if facts.get("oclp_version"):
        parts.append("oclp_v" + oclp_short(facts["oclp_version"]))
    return re.sub(r"[^a-z0-9.,_-]+", "_", "-".join(parts))


def describe(facts):
    """'MacBookPro9,2 / macOS 26.7.1 (25G241) / OCLP 3.0.0-rc.3' (whatever is known)."""
    out = []
    if facts.get("model"):
        out.append(facts["model"])
    if facts.get("macos_version"):
        build = f" ({facts['macos_build']})" if facts.get("macos_build") else ""
        out.append(f"macOS {facts['macos_version']}{build}")
    if facts.get("oclp_version"):
        out.append(f"OCLP {facts['oclp_version']}")
    return " / ".join(out)
