"""Which disk a hao command may erase: tools/ssdpush/ssdinstall.c's checks, as pipeline/stages/golden-image.py's
DiskTarget makes them, with the side of the machine the command writes to:

  external   hao build-disk (the user's SSD) and hao create-installer (the USB stick): an external, physical whole
             disk; USB, Thunderbolt and removable media are what we expect here
  internal   hao install (the Intel Mac's own disk, from the stick's Recovery): an internal, physical, non-removable
             whole disk, as DiskTarget requires

Both refuse anything that is not a whole disk name, a virtual disk (disk image, RAM disk), the disk holding the
running system (or an APFS container on it), a disk holding the command's own inputs, and a disk smaller than the
minimum. The user then has to type the disk's name (or pass it with --confirm). Only diskutil and df are read here.
"""
import plistlib
import re

import livepatch as LP

POLICIES = ('external', 'internal')


def _plist(host, *argv):
    r = host.run(list(argv))
    try:
        return plistlib.loads(r.stdout)
    except Exception:
        raise LP.PatchError('%s: output is not a plist' % ' '.join(argv)) from None


def on(device, names):
    """ssdinstall: a device is on NAME when it is NAME or one of its slices (NAMEsN...)."""
    device = device.removeprefix('/dev/')
    return any(device == name or device.startswith(name + 's') for name in names)


def normalize(disk):
    disk = str(disk)
    return disk[6:] if disk.startswith('/dev/r') else disk[5:] if disk.startswith('/dev/') else disk


def describe(host, disk):
    """(diskutil info of DISK, its APFS containers): what the confirmation names."""
    name = normalize(disk)
    info = _plist(host, 'diskutil', 'info', '-plist', name)
    containers = [row['ContainerReference'] for row in _plist(host, 'diskutil', 'apfs', 'list', '-plist')['Containers']
                  if any(s['DeviceIdentifier'].startswith(name + 's') for s in row.get('PhysicalStores', []))]
    return info, containers


def refusal(host, disk, policy, minimum_bytes, sources=()):
    """The first reason DISK may not be erased under POLICY, or None."""
    if policy not in POLICIES:
        raise ValueError(policy)
    name = normalize(disk)
    if not re.fullmatch(r'disk[0-9]+', name):
        return 'not a whole disk name (diskN)'
    try:
        info, containers = describe(host, name)
    except LP.PatchError:
        return 'diskutil info failed'
    if not info.get('WholeDisk'):
        return 'not a whole disk'
    if info.get('VirtualOrPhysical') == 'Virtual':
        return 'virtual (disk image / RAM disk)'
    internal = bool(info.get('Internal'))
    removable = bool(info.get('Removable') or info.get('RemovableMedia') or info.get('Ejectable'))
    if policy == 'internal':
        if not internal:
            return 'not internal'
        if removable:
            return 'removable'
        if info.get('BusProtocol') == 'USB':
            return 'USB'
    elif internal and not removable:
        return 'internal: this command writes only to an external disk'
    names = [name] + containers
    if on(_plist(host, 'diskutil', 'info', '-plist', '/').get('DeviceIdentifier', ''), names):
        return 'holds the running system'
    size = info.get('TotalSize') or info.get('Size') or 0
    if size < minimum_bytes:
        return 'too small: %d bytes, %d needed' % (size, minimum_bytes)
    if sources:
        lines = host.run(['df', '-P'] + [str(s) for s in sources]).stdout.decode(errors='replace').splitlines()[1:]
        if any(on(line.split()[0], names) for line in lines if line.startswith('/dev/')):
            return 'holds this command\'s own files or inputs'
    return None


def label(info):
    """'disk4  SanDisk Ultra 32.0 GB' for the confirmation line."""
    size = info.get('TotalSize') or info.get('Size') or 0
    return '%s  %s %.1f GB' % (info.get('DeviceIdentifier'), (info.get('MediaName') or info.get('IORegistryEntryName')
                                                              or 'disk').strip(), size / 1e9)
