#!/usr/bin/env python3
"""Seed protected Golden System metadata and group an explicit disk-image pair.

--image is the build's target: a regular image file, or /dev/diskN for a whole-disk target
(golden-image.py DiskTarget); either way every mutation is bound to a container on it.

Run only in a disposable maintenance guest. This helper never creates, formats,
mounts or deletes a volume. System must be the sole volume while seeding; group
accepts only its existing ungrouped System/Data pair. Add Preboot/Recovery later.

Groups the pair through the private APFSContainerVolumeGroupAdd call and sets
xattrs with XATTR_NOFOLLOW. Input attributes come from the selected Golden
filesystem.
"""
import argparse
import ctypes
import errno
import json
import os
from pathlib import Path, PurePosixPath
import plistlib
import re
import stat
import struct
import subprocess
import uuid

ALT_PATH = 'System/Cryptexes/OS'
ALT_NAME = b'com.apple.fs.altlink'


def require(value, message):
    if not value:
        raise ValueError(message)


def plist(*command):
    return plistlib.loads(subprocess.check_output(list(map(str, command))))


def on_disk(device, disk):
    """DEVICE is DISK or one of its slices (diskN, diskNsM, diskNsMsK)."""
    device = device.removeprefix('/dev/')
    return device == disk or device.startswith(disk + 's')


def bound_disk(disk, container):
    """Bind every mutating operation to a container on an explicit physical whole disk, never the running one.

    golden-image.py's DiskTarget accepted the disk (ssdinstall's checks) before formatting it; this re-checks,
    before each System mutation, that the container is on that disk and that the running system is not."""
    disk = str(disk).removeprefix('/dev/')
    require(re.fullmatch(r'disk[0-9]+', disk), 'target must be a whole disk (/dev/diskN)')
    info = plist('diskutil', 'info', '-plist', disk)
    require(info.get('WholeDisk') and info.get('VirtualOrPhysical') != 'Virtual',
            'target disk must be a physical whole disk')
    running = plist('diskutil', 'info', '-plist', '/')
    require(container != running.get('APFSContainerReference'), 'refusing the maintenance container')
    require(not on_disk(running.get('DeviceIdentifier', ''), disk), 'refusing the disk of the running system')
    listing = plist('diskutil', 'apfs', 'list', '-plist', container)['Containers']
    require(len(listing) == 1, 'ambiguous container')
    listing = listing[0]
    stores = listing['PhysicalStores']
    require(len(stores) == 1, 'expected one physical store')
    require(on_disk(stores[0]['DeviceIdentifier'], disk) and stores[0]['DeviceIdentifier'] != disk,
            'container/store do not belong to the explicit disk')
    if running.get('APFSContainerReference'):   # the running System's own stores, when it is APFS
        for row in plist('diskutil', 'apfs', 'list', '-plist', running['APFSContainerReference'])['Containers']:
            for store in row['PhysicalStores']:
                require(not on_disk(store['DeviceIdentifier'], disk), 'refusing the disk of the running system')
    return listing


def bound_container(image, container):
    """Bind every mutating operation to an attached regular image, never boot media.
    A target of the form /dev/diskN is a whole disk instead (golden-image.py DiskTarget): bound_disk."""
    if re.fullmatch(r'/dev/disk[0-9]+', str(image)):
        return bound_disk(image, container)
    image = Path(image).resolve()
    require(image.is_file(), 'image must be a regular file')
    running = plist('diskutil', 'info', '-plist', '/')
    require(container != running.get('APFSContainerReference'), 'refusing the maintenance container')
    listing = plist('diskutil', 'apfs', 'list', '-plist', container)['Containers']
    require(len(listing) == 1, 'ambiguous container')
    listing = listing[0]
    stores = listing['PhysicalStores']
    require(len(stores) == 1, 'expected one disk-image store')
    store = stores[0]['DeviceIdentifier']
    matches = [item for item in plist('hdiutil', 'info', '-plist')['images']
               if Path(item['image-path']).resolve() == image]
    require(len(matches) == 1 and matches[0].get('writeable'), 'image is not attached writable exactly once')
    devices = {item.get('dev-entry', '').removeprefix('/dev/') for item in matches[0]['system-entities']}
    require(container in devices and store in devices, 'container/store do not belong to the explicit image')
    return listing


def topology(image, system_root, data=None):
    system_root = Path(system_root).resolve()
    require(system_root != Path('/'), 'refusing the running root')
    info = plist('diskutil', 'info', '-plist', system_root)
    system, container = info['DeviceIdentifier'], info['APFSContainerReference']
    require(info.get('MountPoint') == str(system_root) and not info.get('APFSSnapshot'),
            'System must be its writable volume mount')
    listing = bound_container(image, container)
    wanted = {system: ['System']}
    if data: wanted[data] = ['Data']
    volumes = {item['DeviceIdentifier']: item for item in listing['Volumes']}
    require(set(volumes) == set(wanted), 'expected sole System, or only the explicit System/Data pair')
    rows = {}
    for device, role in wanted.items():
        require(volumes[device]['Roles'] == role, 'incorrect APFS volume role')
        row = plist('diskutil', 'info', '-plist', device)
        require(row['APFSContainerReference'] == container and not row.get('APFSSnapshot'), 'volume identity changed')
        if data and device == data:
            require(not row.get('MountPoint'), 'Data must be unmounted during grouping')
        rows[device] = dict(uuid=row['VolumeUUID'].upper(), group=row.get('APFSVolumeGroupID'), role=role[0])
    return dict(container=container, system=system, volumes=rows)


def read_xattr(path):
    lib = ctypes.CDLL(None, use_errno=True)
    lib.getxattr.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_void_p,
                            ctypes.c_size_t, ctypes.c_uint32, ctypes.c_int]
    lib.getxattr.restype = ctypes.c_ssize_t
    size = lib.getxattr(os.fsencode(path), ALT_NAME, None, 0, 0, 1)
    if size < 0: raise OSError(ctypes.get_errno(), os.strerror(ctypes.get_errno()), str(path))
    body = ctypes.create_string_buffer(size)
    count = lib.getxattr(os.fsencode(path), ALT_NAME, body, size, 0, 1)
    if count != size: raise OSError(ctypes.get_errno(), 'altlink read changed', str(path))
    return body.raw


def write_xattr(path, value):
    lib = ctypes.CDLL(None, use_errno=True)
    lib.setxattr.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_void_p,
                            ctypes.c_size_t, ctypes.c_uint32, ctypes.c_int]
    lib.setxattr.restype = ctypes.c_int
    # XATTR_NOFOLLOW | XATTR_CREATE: never replace an unexpected attribute.
    if lib.setxattr(os.fsencode(path), ALT_NAME, value, len(value), 0, 1 | 2):
        raise OSError(ctypes.get_errno(), os.strerror(ctypes.get_errno()), str(path))


def altlink(system_root):
    path = Path(system_root) / ALT_PATH
    info = path.lstat()
    require(stat.S_ISLNK(info.st_mode), 'OS cryptex path must be a symlink')
    return dict(inode=info.st_ino, device=info.st_dev, target=os.readlink(path),
                altlink_hex=read_xattr(path).hex())


def seed(image, system_root, link_target, value, observe=topology):
    state = observe(image, system_root)
    require(all(not row['group'] for row in state['volumes'].values()), 'System must be sole and ungrouped')
    path = Path(system_root) / ALT_PATH
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
    if not os.path.lexists(path): path.symlink_to(link_target)
    require(path.is_symlink() and os.readlink(path) == link_target, 'existing OS link differs')
    try:
        current = read_xattr(path)
    except OSError as error:
        if error.errno != errno.ENOATTR: raise  # Darwin ENOATTR; never waive permission/read errors.
        write_xattr(path, value)
    else:
        require(current == value, 'existing OS altlink differs')
    result = altlink(system_root)
    require(result['target'] == link_target and result['altlink_hex'] == value.hex(), 'altlink readback differs')
    require(observe(image, system_root) == state, 'System topology changed during seed')
    return result


def group_add(container, indices):
    """Archived Apple ABI; CFArray members live through the synchronous call."""
    cf = ctypes.CDLL('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
    cf.CFNumberCreate.argtypes = [ctypes.c_void_p, ctypes.c_long, ctypes.c_void_p]
    cf.CFNumberCreate.restype = ctypes.c_void_p
    cf.CFArrayCreate.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_long, ctypes.c_void_p]
    cf.CFArrayCreate.restype = ctypes.c_void_p
    cf.CFRelease.argtypes = [ctypes.c_void_p]
    numbers, array = [], None
    try:
        for index in indices:
            number = cf.CFNumberCreate(None, 1, ctypes.byref(ctypes.c_int8(index)))
            require(number, 'CFNumber allocation failed')
            numbers.append(number)
        array = cf.CFArrayCreate(None, (ctypes.c_void_p * len(numbers))(*numbers), len(numbers), None)
        require(array, 'CFArray allocation failed')
        apfs = ctypes.CDLL('/System/Library/PrivateFrameworks/APFS.framework/APFS')
        apfs.APFSContainerVolumeGroupAdd.argtypes = [ctypes.c_char_p, ctypes.c_void_p, ctypes.c_void_p]
        apfs.APFSContainerVolumeGroupAdd.restype = ctypes.c_int32
        output = (ctypes.c_ubyte * 16)()
        result = apfs.APFSContainerVolumeGroupAdd(container.encode(), array, output)
        require(result == 0, f'APFSContainerVolumeGroupAdd failed: {result:#x}')
        return str(uuid.UUID(bytes=bytes(output))).upper()
    finally:
        if array: cf.CFRelease(array)
        for number in numbers: cf.CFRelease(number)


def group(image, system_root, data, observe=topology, read=altlink, mutate=group_add):
    before = observe(image, system_root, data)
    require(all(not row['group'] for row in before['volumes'].values()), 'pair must be ungrouped')
    original = read(system_root)
    container = before['container']
    indices = []
    for device in (before['system'], data):
        match = re.fullmatch(re.escape(container) + r's([0-9]+)', device)
        require(match and 1 <= int(match[1]) <= 127, 'volume index outside explicit container')
        indices.append(int(match[1]))
    expected = before['volumes'][data]['uuid']
    returned = mutate(container, indices)
    require(returned == expected, 'group call returned a different Data UUID')
    after = observe(image, system_root, data)
    wanted = json.loads(json.dumps(before))
    for row in wanted['volumes'].values(): row['group'] = expected
    require(after == wanted, 'group/volume readback differs')
    require(read(system_root) == original, 'altlink inode, target or bytes changed during grouping')
    return dict(group_uuid=returned, topology=after, altlink=original)


FIRMLINK_NAME = b'com.apple.fs.firmlink'
SF_FIRMLINK = 0x00800000
UF_COMPRESSED = 0x20


class Attrlist(ctypes.Structure):
    _fields_ = [('bitmapcount',ctypes.c_uint16),('reserved',ctypes.c_uint16),
                ('commonattr',ctypes.c_uint32),('volattr',ctypes.c_uint32),
                ('dirattr',ctypes.c_uint32),('fileattr',ctypes.c_uint32),('forkattr',ctypes.c_uint32)]


def source_xattrs(path):
    lib = ctypes.CDLL(None,use_errno=True)
    lib.listxattr.argtypes = [ctypes.c_char_p,ctypes.c_void_p,ctypes.c_size_t,ctypes.c_int]
    lib.listxattr.restype = ctypes.c_ssize_t
    lib.getxattr.argtypes = [ctypes.c_char_p,ctypes.c_char_p,ctypes.c_void_p,ctypes.c_size_t,ctypes.c_uint32,ctypes.c_int]
    lib.getxattr.restype = ctypes.c_ssize_t
    name = os.fsencode(path)
    length = lib.listxattr(name,None,0,1)
    if length < 0: raise OSError(ctypes.get_errno(),os.strerror(ctypes.get_errno()),str(path))
    names = ctypes.create_string_buffer(length)
    if length and lib.listxattr(name,names,length,1) != length:
        raise OSError(ctypes.get_errno(),'xattr list changed',str(path))
    result = {}
    for key in names.raw.split(b'\0'):
        if not key: continue
        size = lib.getxattr(name,key,None,0,0,1)
        if size < 0: raise OSError(ctypes.get_errno(),os.strerror(ctypes.get_errno()),str(path))
        body = ctypes.create_string_buffer(size)
        if size and lib.getxattr(name,key,body,size,0,1) != size:
            raise OSError(ctypes.get_errno(),'xattr read changed',str(path))
        result[key] = body.raw
    return result


def copy_times(source, target):
    # Darwin attr.h: creation, modification and access times, in bit order.
    attributes = Attrlist(5,0,0x200 | 0x400 | 0x1000,0,0,0,0)
    lib = ctypes.CDLL(None,use_errno=True)
    lib.getattrlist.argtypes = [ctypes.c_char_p,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_size_t,ctypes.c_ulong]
    lib.setattrlist.argtypes = lib.getattrlist.argtypes
    buffer = ctypes.create_string_buffer(4+3*16)
    if lib.getattrlist(os.fsencode(source),ctypes.byref(attributes),buffer,len(buffer),1):
        raise OSError(ctypes.get_errno(),os.strerror(ctypes.get_errno()),str(source))
    if struct.unpack_from('=I',buffer.raw)[0] != len(buffer):
        raise ValueError('unexpected Darwin timestamp attribute length')
    times = ctypes.create_string_buffer(buffer.raw[4:],3*16)
    if lib.setattrlist(os.fsencode(target),ctypes.byref(attributes),times,len(times),1):
        raise OSError(ctypes.get_errno(),os.strerror(ctypes.get_errno()),str(target))


def metadata_flags(source_flags, target_flags, firmlink):
    # Compression is storage state, as in pristine hmeta. Only declared firmlink
    # anchors defer their SF_FIRMLINK bit to the entitled APFS fsctl later.
    mask = UF_COMPRESSED | (SF_FIRMLINK if firmlink else 0)
    return (source_flags & ~mask) | (target_flags & UF_COMPRESSED)


def copy_metadata(source, target, firmlink=False):
    value = source.lstat()
    lib = ctypes.CDLL(None,use_errno=True)
    os.lchown(target,value.st_uid,value.st_gid)
    if target.is_symlink():
        lib.lchmod.argtypes = [ctypes.c_char_p,ctypes.c_uint16]
        result = lib.lchmod(os.fsencode(target),stat.S_IMODE(value.st_mode))
        if result: raise OSError(ctypes.get_errno(),os.strerror(ctypes.get_errno()),str(target))
    else:
        os.chmod(target,stat.S_IMODE(value.st_mode))
    # copyfile.h: COPYFILE_ACL | COPYFILE_NOFOLLOW. Do not ask copyfile to
    # synthesize the source stat flags or protected firmlink xattr.
    lib.copyfile.argtypes = [ctypes.c_char_p,ctypes.c_char_p,ctypes.c_void_p,ctypes.c_uint32]
    if lib.copyfile(os.fsencode(source),os.fsencode(target),None,1 | (1<<18) | (1<<19)):
        raise OSError(ctypes.get_errno(),os.strerror(ctypes.get_errno()),str(target))
    lib.setxattr.argtypes = [ctypes.c_char_p,ctypes.c_char_p,ctypes.c_void_p,ctypes.c_size_t,ctypes.c_uint32,ctypes.c_int]
    for name, body in source_xattrs(source).items():
        if firmlink and name == FIRMLINK_NAME: continue
        if lib.setxattr(os.fsencode(target),name,body,len(body),0,1):
            raise OSError(ctypes.get_errno(),os.strerror(ctypes.get_errno()),str(target))
    copy_times(source,target)
    os.chflags(target,metadata_flags(value.st_flags,target.lstat().st_flags,firmlink),follow_symlinks=False)


def firmlink_paths(source):
    result = set()
    for line in (source/'usr/share/firmlinks').read_text().splitlines():
        if not line.strip() or line.startswith('#'): continue
        original, target = line.split()[:2]
        parts, destination = PurePosixPath(original), PurePosixPath(target)
        require(original.startswith('/') and not original.startswith('//') and
                '..' not in parts.parts and str(parts) == original and original != '/',
                'invalid firmlink source: '+original)
        require(not destination.is_absolute() and '..' not in destination.parts and
                str(destination) == target and target != '.', 'invalid firmlink target: '+target)
        result.add(original.lstrip('/'))
    return result


def copy_system_tree(source, target, copy=copy_metadata, command=subprocess.run):
    """Copy ordinary subtrees intact; defer only declared empty firmlink objects."""
    anchors = firmlink_paths(source)
    counts = dict(copied_subtrees=0,copied_symlinks=0,metadata_directories=0,firmlink_anchors=[])
    def visit(src, dst, relative=''):
        anchor = relative in anchors
        ancestor = not relative or any(name.startswith(relative+'/') for name in anchors)
        if anchor:
            require(src.is_dir() and not src.is_symlink(),'firmlink anchor is not a directory: '+relative)
            require(next(src.iterdir(),None) is None,'firmlink anchor has source contents: '+relative)
            dst.mkdir(mode=0o755,parents=True,exist_ok=True)
            copy(src,dst,True)
            counts['firmlink_anchors'].append(relative)
        elif src.is_symlink():
            dst.symlink_to(os.readlink(src))
            copy(src,dst,False)
            counts['copied_symlinks'] += 1
        elif ancestor:
            require(src.is_dir(),'firmlink ancestor is not a directory: '+relative)
            dst.mkdir(mode=0o755,parents=True,exist_ok=True)
            for child in sorted(src.iterdir()):
                visit(child,dst/child.name,'/'.join(filter(None,(relative,child.name))))
            # Apply directory protections/timestamps after all children.
            copy(src,dst,False)
            counts['metadata_directories'] += 1
        else:
            command(['ditto','--rsrc','--extattr','--acl',str(src),str(dst)],check=True)
            counts['copied_subtrees'] += 1
    visit(source,target)
    return counts


def copy_system(image, system_root, source):
    state = topology(image,system_root)
    require(all(not row['group'] for row in state['volumes'].values()),'System must be sole and ungrouped')
    return copy_system_tree(source,system_root)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    for name in ('check-system', 'seed', 'copy-altlink', 'copy-system', 'group'):
        command = sub.add_parser(name)
        command.add_argument('--image', type=Path, required=True)
        command.add_argument('--system', type=Path, required=True)
        if name == 'seed':
            command.add_argument('--link-target', required=True)
            command.add_argument('--altlink-hex', required=True)
        elif name in ('copy-altlink','copy-system'): command.add_argument('--source', type=Path, required=True)
        elif name == 'group': command.add_argument('--data', required=True)
    args = parser.parse_args()
    if args.command == 'check-system':
        result = topology(args.image, args.system)
        require(all(not row['group'] for row in result['volumes'].values()), 'System must be ungrouped')
    elif args.command == 'seed': result = seed(args.image, args.system, args.link_target, bytes.fromhex(args.altlink_hex))
    elif args.command == 'copy-system': result = copy_system(args.image,args.system,args.source)
    elif args.command == 'copy-altlink':
        result = seed(args.image, args.system, os.readlink(args.source), read_xattr(args.source))
    else: result = group(args.image, args.system, args.data)
    print(json.dumps(result, indent=2))


if __name__ == '__main__': main()
