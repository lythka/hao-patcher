"""Workspace identity/format ordering through pure plist/command fixtures, never host APFS."""
import copy
import json
import os
from pathlib import Path
import plistlib
import runpy
import subprocess
import tempfile
import unittest
from unittest.mock import patch

MODULE=runpy.run_path(str(Path(__file__).resolve().parents[1]/'stages/golden-workspace.py'))
SIZE=160*1024**3
BOOT=dict(ContainerReference='disk3',PhysicalStores=[dict(DeviceIdentifier='disk0s2')],
          Volumes=[dict(DeviceIdentifier='disk3s1',APFSVolumeUUID='BOOT')])
ROOT=dict(DeviceIdentifier='disk3s1s1',ParentWholeDisk='disk3',APFSContainerReference='disk3',VolumeUUID='BOOT')
PHYSICAL=dict(AllDisksAndPartitions=[dict(DeviceIdentifier='disk8')])
INFO=dict(DeviceIdentifier='disk8',WholeDisk=True,Internal=False,Writable=True,BusProtocol='USB',TotalSize=SIZE)


class FakeCommands:
    def __init__(self, fail_format=False, wrong_mount=False):
        self.entries=[];self.apfs_count=0;self.fail_format=fail_format;self.wrong_mount=wrong_mount

    def run(self,args):
        args=list(map(str,args));self.entries.append(dict(command=args))
        if args==['diskutil','list','-plist','external','physical']:value=PHYSICAL
        elif args==['diskutil','info','-plist','/']:value=ROOT
        elif args==['diskutil','info','-plist','disk8']:value=INFO
        elif args==['diskutil','apfs','list','-plist']:
            self.apfs_count+=1
            value=dict(Containers=[BOOT])
            if self.apfs_count>1:value['Containers']=value['Containers']+[
                dict(ContainerReference='disk11',PhysicalStores=[dict(DeviceIdentifier='disk8s2')],
                     Volumes=[dict(DeviceIdentifier='disk11s1',APFSVolumeUUID='WORKSPACE')])]
        elif args[:2]==['diskutil','partitionDisk']:
            if self.fail_format:raise subprocess.CalledProcessError(1,args)
            return b''
        elif args[:3]==['diskutil','info','-plist']:
            value=dict(DeviceIdentifier='disk11s1',VolumeUUID='WRONG' if self.wrong_mount else 'WORKSPACE')
        else:return b''
        return plistlib.dumps(value)


class Workspace(unittest.TestCase):
    def select(self, infos, physical=PHYSICAL,root=ROOT,containers=None):
        return MODULE['select_disk'](physical,infos,root,containers or dict(Containers=[BOOT]),SIZE)

    def test_only_exact_writable_usb_from_external_physical_list_is_selected(self):
        self.assertEqual('disk8',self.select([INFO]))
        for replacement in [dict(TotalSize=SIZE-512),dict(WholeDisk=False),dict(Internal=True),
                            dict(Writable=False),dict(BusProtocol='PCI-Express'),dict(DeviceIdentifier='disk9')]:
            altered=dict(INFO,**replacement)
            with self.subTest(replacement=replacement),self.assertRaises(ValueError):self.select([altered])

    def test_live_builder_usb_plist_selects_its_declared_workspace(self):
        capture=json.loads((Path(__file__).parent/'fixtures/golden-workspace-usb.json').read_text())
        self.assertEqual('disk6',self.select(capture['disk_information'],capture['physical'],
                                          capture['root'],capture['apfs_before']))

    def test_ambiguous_same_size_usb_does_not_authorize_format(self):
        other=dict(INFO,DeviceIdentifier='disk9')
        physical=dict(AllDisksAndPartitions=[dict(DeviceIdentifier='disk8'),dict(DeviceIdentifier='disk9')])
        with self.assertRaisesRegex(ValueError,'expected one'):self.select([INFO,other],physical)

    def test_apfs_boot_backing_store_is_protected_even_if_reported_external(self):
        physical=dict(AllDisksAndPartitions=[dict(DeviceIdentifier='disk0')])
        with self.assertRaises(ValueError):self.select([dict(INFO,DeviceIdentifier='disk0')],physical)

    def test_missing_boot_identity_is_refused_and_missing_uuid_does_not_match_every_volume(self):
        with self.assertRaisesRegex(ValueError,'boot disk identity'):self.select([INFO],root={})
        unrelated=dict(ContainerReference='disk12',PhysicalStores=[dict(DeviceIdentifier='disk8s2')],
                       Volumes=[dict(DeviceIdentifier='disk12s1')])
        self.assertEqual('disk8',self.select([INFO],root=dict(DeviceIdentifier='disk3s1s1',APFSContainerReference='disk3'),
                                           containers=dict(Containers=[BOOT,unrelated])))

    def test_guest_format_and_explicit_mount_order_and_record(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'workspace.json';commands=FakeCommands()
            record=MODULE['prepare'](SIZE,Path('/Volumes/unit-fixture'),'fixture',path,'a'*64,commands)
            self.assertEqual('mounted',record['status'])
            calls=[entry['command'] for entry in commands.entries]
            format_index=next(i for i,cmd in enumerate(calls) if cmd[:2]==['diskutil','partitionDisk'])
            self.assertEqual(['diskutil','partitionDisk','/dev/disk8','GPT','APFS','fixture','100%'],calls[format_index])
            mount_index=next(i for i,cmd in enumerate(calls) if cmd[:2]==['diskutil','mount'])
            self.assertGreater(mount_index,format_index)
            self.assertIn(['install','-d','-m','755','/Volumes/unit-fixture'],calls)
            self.assertIn(['chmod','755','/Volumes/unit-fixture'],calls)
            self.assertEqual('WORKSPACE',json.loads(path.read_text())['mounted']['VolumeUUID'])
            self.assertEqual('a'*64,record['source_sha256'])

    def test_format_failure_records_evidence_and_never_mounts(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'workspace.json';commands=FakeCommands(fail_format=True)
            with self.assertRaises(subprocess.CalledProcessError):MODULE['prepare'](SIZE,Path('/Volumes/fixture'),'fixture',path,'a'*64,commands)
            self.assertEqual('failed',json.loads(path.read_text())['status'])
            self.assertFalse(any(entry['command'][:2]==['diskutil','mount'] for entry in commands.entries))

    def test_wrong_mounted_volume_is_not_accepted(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'workspace.json'
            with self.assertRaisesRegex(ValueError,'does not match'):MODULE['prepare'](SIZE,Path('/Volumes/fixture'),'fixture',path,'a'*64,FakeCommands(wrong_mount=True))
            self.assertEqual('failed',json.loads(path.read_text())['status'])

    def test_formatter_cannot_run_on_host_without_owned_guest_agent(self):
        with patch.dict(os.environ,{},clear=True),patch('subprocess.run') as command:
            with self.assertRaisesRegex(ValueError,'refusing host execution'):
                MODULE['prepare'](SIZE,Path('/Volumes/fixture'),'fixture','unused.json','a'*64)
            command.assert_not_called()


if __name__=='__main__':unittest.main()
