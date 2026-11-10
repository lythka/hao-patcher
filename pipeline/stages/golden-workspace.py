#!/usr/bin/env python3
"""Identify and format the one declared scratch USB inside a maintenance guest."""
import argparse
import datetime
import json
import os
from pathlib import Path
import plistlib
import re
import subprocess


def whole_disk(identifier):
    match = re.match(r'^(disk[0-9]+)(?:s[0-9]+)*$', identifier.removeprefix('/dev/'))
    return match.group(1) if match else None


def root_disks(root, containers):
    references = {root.get(key) for key in ('DeviceIdentifier','ParentWholeDisk','APFSContainerReference','APFSVolumeDisk')}
    references.discard(None)
    if not references:
        raise ValueError('maintenance boot disk identity is unavailable')
    protected = {whole_disk(value) for value in references}
    for container in containers.get('Containers', []):
        if (container['ContainerReference'] in references or
                any((bool(root.get('VolumeUUID')) and volume.get('APFSVolumeUUID') == root['VolumeUUID']) or
                    any(value == volume['DeviceIdentifier'] or value.startswith(volume['DeviceIdentifier']+'s')
                        for value in references)
                    for volume in container.get('Volumes', []))):
            protected.update(whole_disk(store['DeviceIdentifier']) for store in container['PhysicalStores'])
    return protected - {None}


def select_disk(physical, information, root, containers, expected_bytes):
    protected = root_disks(root, containers)
    listed = {row['DeviceIdentifier'] for row in physical['AllDisksAndPartitions']}
    candidates = []
    for info in information:
        identifier = info['DeviceIdentifier']
        size = info.get('TotalSize', info.get('DiskSize'))
        if (identifier in listed and re.fullmatch(r'disk[0-9]+',identifier) and identifier not in protected
                and info.get('WholeDisk') is True and info.get('Internal') is False
                and info.get('Writable') is True and info.get('BusProtocol') == 'USB'
                and size == expected_bytes):
            candidates.append(identifier)
    if len(candidates) != 1:
        raise ValueError('expected one writable external physical USB disk of %d bytes; found %s (boot %s)' %
                         (expected_bytes,candidates,sorted(protected)))
    return candidates[0]


class Commands:
    def __init__(self):
        self.entries = []

    def run(self, args):
        result = subprocess.run(list(map(str,args)),stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        self.entries.append(dict(command=list(map(str,args)),returncode=result.returncode,
                                 stdout=result.stdout.decode(errors='replace'),stderr=result.stderr.decode(errors='replace')))
        result.check_returncode()
        return result.stdout


def prepare(size, mount, name, record_path, source_sha256, commands=None):
    if commands is None and not re.fullmatch(r'[a-f0-9]{32}',os.environ.get('SSDVM_AGENT_ID','')):
        raise ValueError('workspace formatting requires the owned SSDVM guest agent; refusing host execution')
    commands = commands or Commands()
    mount = Path(mount)
    record = dict(operation='golden-workspace',expected_size_bytes=size,mount=str(mount),name=name,
                  source_sha256=source_sha256,date=datetime.datetime.now(datetime.timezone.utc).isoformat(),status='identifying')
    def plist(*args):
        return plistlib.loads(commands.run(args))
    try:
        physical = plist('diskutil','list','-plist','external','physical')
        root = plist('diskutil','info','-plist','/')
        containers = plist('diskutil','apfs','list','-plist')
        information = [plist('diskutil','info','-plist',row['DeviceIdentifier']) for row in physical['AllDisksAndPartitions']]
        record.update(physical=physical,root=root,apfs_before=containers,disk_information=information)
        disk = select_disk(physical,information,root,containers,size)
        record['device'] = disk
        commands.run(['diskutil','partitionDisk','/dev/'+disk,'GPT','APFS',name,'100%'])
        after = plist('diskutil','apfs','list','-plist')
        matching = [container for container in after['Containers']
                    if any(whole_disk(store['DeviceIdentifier']) == disk for store in container['PhysicalStores'])]
        container, = matching
        volume, = container['Volumes']
        device = volume['DeviceIdentifier']
        commands.run(['diskutil','unmount',device])
        commands.run(['install','-d','-m','755',mount])
        commands.run(['diskutil','mount','-mountOptions','nobrowse','-mountPoint',mount,device])
        commands.run(['diskutil','enableOwnership',mount])
        commands.run(['chmod','755',mount])
        commands.run(['chown','root:wheel',mount])
        mounted = plist('diskutil','info','-plist',mount)
        if mounted['DeviceIdentifier'] != device or mounted['VolumeUUID'] != volume['APFSVolumeUUID']:
            raise ValueError('mounted workspace does not match the freshly created volume')
        record.update(status='mounted',volume=volume,mounted=mounted,apfs_after=after)
    except BaseException as error:
        record.update(status='failed',error=str(error))
        raise
    finally:
        record['commands'] = commands.entries
        Path(record_path).write_text(json.dumps(record,indent=2)+'\n')
    print('Workspace: /dev/'+disk+' at '+str(mount),flush=True)
    return record


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--size-bytes',type=int,required=True)
    parser.add_argument('--mount',type=Path,required=True)
    parser.add_argument('--name',required=True)
    parser.add_argument('--record',type=Path,required=True)
    parser.add_argument('--source-sha256',required=True)
    args=parser.parse_args()
    prepare(args.size_bytes,args.mount,args.name,args.record,args.source_sha256)


if __name__=='__main__':main()
