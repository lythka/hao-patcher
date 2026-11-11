"""Install Tahoe's CoreWiFi as an on-disk override of the cached 27.x CoreWiFi (golden-gate-corewifi, kc-hook).

    install.py SYSTEM_MOUNT CORE_WIFI_TAHOE CORE_WIFI_COMPAT

Writes exactly two files into SYSTEM_MOUNT/System/Library/PrivateFrameworks/CoreWiFi.framework/Versions/A, whose
Resources the base layers already installed: CoreWiFiTahoe.dylib (Tahoe 25G342's CoreWiFi, cut from its shared cache)
and CoreWiFi (the compat library, which re-exports it). dyld finds a file at a cached library's path, loads it instead
of the cached copy and repoints every cached client's bindings to it, the same way OCLP's IO80211 and WiFiPeerToPeer
files already override their cached libraries on this image.

Before writing it checks both files: x86_64 dylibs with the expected install names, the compat library re-exporting
CoreWiFiTahoe.dylib, and no CoreWiFi binary already in the framework (a differing one stops the install). It edits no
other file; airportd and every program keep their own identity.
"""
import hashlib
import os
from pathlib import Path
import shutil
import struct
import sys

FRAMEWORK = 'System/Library/PrivateFrameworks/CoreWiFi.framework/Versions/A'
IMAGE = '/' + FRAMEWORK + '/CoreWiFi'
TAHOE = '/' + FRAMEWORK + '/CoreWiFiTahoe.dylib'
LC_ID_DYLIB, LC_REEXPORT_DYLIB = 0xD, 0x8000001F
SF_RESTRICTED = 0x80000


def dylib_names(path):
    """(install name, [re-exported install names]) of a thin x86_64 dylib."""
    data = Path(path).read_bytes()
    magic, cputype, _, filetype, ncmds, _, _, _ = struct.unpack_from('<IiiIIIII', data)
    if magic != 0xFEEDFACF or cputype != 0x01000007 or filetype != 6:
        raise SystemExit(f'{path}: not a thin x86_64 dylib')
    at, ident, reexports = 32, None, []
    for _ in range(ncmds):
        cmd, size = struct.unpack_from('<II', data, at)
        if cmd in (LC_ID_DYLIB, LC_REEXPORT_DYLIB):
            off, = struct.unpack_from('<I', data, at + 8)
            name = data[at + off:at + size].split(b'\0', 1)[0].decode()
            if cmd == LC_ID_DYLIB:
                ident = name
            else:
                reexports.append(name)
        at += size
    return ident, reexports


def plan(system, tahoe, compat):
    """[(source, target)] after checking the inputs and the target framework."""
    folder = Path(system)/FRAMEWORK
    if not folder.is_dir():
        raise SystemExit(f'{folder}: no CoreWiFi framework folder (the base layers install its Resources)')
    if dylib_names(tahoe) != (TAHOE, []):
        raise SystemExit(f'{tahoe}: install name must be {TAHOE}, re-exporting nothing')
    ident, reexports = dylib_names(compat)
    if ident != IMAGE or reexports != [TAHOE]:
        raise SystemExit(f'{compat}: must be {IMAGE} re-exporting {TAHOE}, is {ident} re-exporting {reexports}')
    pairs = [(Path(tahoe), folder/'CoreWiFiTahoe.dylib'), (Path(compat), folder/'CoreWiFi')]
    for source, target in pairs:
        if target.exists() and target.read_bytes() != source.read_bytes():
            raise SystemExit(f'{target} already exists with other contents; refusing to replace it')
    return pairs


def install(system, tahoe, compat):
    for source, target in plan(system, tahoe, compat):
        if target.exists():
            os.chflags(target, 0)
        shutil.copyfile(source, target)
        if os.geteuid() == 0:
            os.chown(target, 0, 0)
        os.chmod(target, 0o755)
        if os.geteuid() == 0:
            os.chflags(target, SF_RESTRICTED)
        print(hashlib.sha256(target.read_bytes()).hexdigest(), target.relative_to(system), flush=True)


if __name__ == '__main__':
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    install(Path(sys.argv[1]), sys.argv[2], sys.argv[3])
