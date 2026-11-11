"""Guest assembly sequencing with regular files and fake commands; no APFS calls."""
import importlib.util
import json
from pathlib import Path
import plistlib
import shutil
import subprocess
import tempfile
import unittest

SCRIPT=Path(__file__).resolve().parents[1]/'stages/golden-image.py'
spec=importlib.util.spec_from_file_location('golden_image',SCRIPT)
golden=importlib.util.module_from_spec(spec);spec.loader.exec_module(golden)


class FakeCommands:
    def __init__(self,config,fail=None):
        self.config=config;self.fail=fail;self.entries=[];self.volumes=[];self.grouped=False;self.displace_preboot=False

    def run(self,command,env=None,capture=False):
        a=list(map(str,command));self.entries.append(dict(command=a,environment=dict(env or {})))
        if self.fail and self.fail(a):raise subprocess.CalledProcessError(19,a)
        if a[0]=='hdiutil':
            return plistlib.dumps({'system-entities':[{'dev-entry':'/dev/disk70'}]}) if a[1]=='attach' else b''
        if a[:2]==['diskutil','partitionDisk']:
            self.volumes=[dict(DeviceIdentifier='disk71s1',VolumeUUID='SYSTEM-UUID',Name='Golden',Roles=[])]
        elif a[:4]==['diskutil','apfs','list','-plist']:
            return plistlib.dumps({'Containers':[dict(ContainerReference='disk71',PhysicalStores=[{'DeviceIdentifier':'disk70s2'}],Volumes=self.volumes)]})
        elif a[:3]==['diskutil','apfs','changeVolumeRole']:
            raise AssertionError('APFS cannot promote an ordinary volume to System')
        elif a[:3]==['diskutil','apfs','addVolume']:
            key={'S':'System','D':'Data','B':'Preboot','R':'Recovery','V':'VM'}[a[a.index('-role')+1]]
            if key not in ('System','Data') and not self.grouped:raise AssertionError('extra volume before group')
            if key=='Data' and not any(x['command'][0]=='sh' and x['command'][2]=='system' for x in self.entries):raise AssertionError('Data created before Golden System copy')
            self.volumes.append(dict(DeviceIdentifier='disk71s'+str(max(int(v['DeviceIdentifier'].split('s')[-1]) for v in self.volumes)+1),VolumeUUID=key.upper()+'-UUID',Name=a[5],Roles=[key]))
        elif a[:3]==['diskutil','apfs','deleteVolume']:
            self.volumes=[v for v in self.volumes if v['DeviceIdentifier']!=a[-1]]
        elif a[:2]==['diskutil','mount']:
            for row in self.volumes:
                if row['DeviceIdentifier']==a[-1]:row['MountPoint']=a[a.index('-mountPoint')+1]
        elif a[:2]==['diskutil','enableOwnership'] and Path(a[-1]).name=='esp':
            raise AssertionError('FAT EFI volume does not support Unix ownership')
        elif a[:2]==['diskutil','unmount']:
            for row in self.volumes:
                if a[-1] in (row['DeviceIdentifier'],row.get('MountPoint')):row['MountPoint']=''
        elif a[:3]==['diskutil','apfs','updatePreboot'] and self.displace_preboot:
            for row in self.volumes:
                if row['Roles']==['Preboot']:row['MountPoint']='/Volumes/UnexpectedPreboot'
        elif a[:3]==['diskutil','info','-plist']:
            return plistlib.dumps(next(row for row in self.volumes if row['DeviceIdentifier']==a[3]))
        elif a[0]==self.config['python'] and a[2]==self.config['layout_helper']:
            assert a[3]=='group'
            assert len(self.volumes)==2 and a[-1]==self.volumes[1]['DeviceIdentifier']
            assert not any(x['command'][:2]==['diskutil','mount'] and x['command'][-1]==a[-1] for x in self.entries)
            self.grouped=True
            return json.dumps({'group_uuid':'DATA-UUID'}).encode()
        elif a[0]=='sh' and a[1]==self.config['pristine_script']:
            system=Path(self.config['work'])/'mnt/system'
            if a[2]=='system':
                assert len(self.volumes)==1 and self.volumes[0]['Roles']==['System']
                p=system/'System/Library/CoreServices';p.mkdir(parents=True);(p/'SystemVersion.plist').write_bytes(plistlib.dumps({'ProductVersion':'27.0','ProductBuildVersion':'26A428'}))
                p=system/'System/Cryptexes';p.mkdir(parents=True);(p/'OS').symlink_to('../../System/Volumes/Preboot/Cryptexes/OS')
                self.inode=(p/'OS').lstat().st_ino
            else:
                assert self.grouped and '--kdk-preparer' in a
                assert (system/'System/Cryptexes/OS').lstat().st_ino==self.inode
        elif a[0]==self.config['python'] and a[2]==self.config['donor_tool']:
            assert a[3]=='install-role'
            role=a[a.index('--role')+1]
            artifact=Path(a[a.index('--artifact')+1]);target=Path(a[a.index('--destination')+1])
            assert target.name=='DATA-UUID'
            shutil.copytree(artifact/'payload'/role,target,dirs_exist_ok=True)
        elif a[:3]==['sh','-eu',self.config['kernel_hook']]:
            (Path(a[3])/'System/Library/KernelCollections/BootKernelExtensions.kc').write_bytes(b'authored collection')
        elif a[0]=='ditto':
            source,target=map(Path,a[-2:])
            if source.is_dir():shutil.copytree(source,target,dirs_exist_ok=True)
            else:shutil.copy2(source,target)
        elif a[0]=='sh' and a[1]=='-eu' and a[2]==self.config['posts'][0]['script']:
            output=Path(env['PATCH_OUTPUT_DIR'])
            assert output.is_dir()
            (output/'fixture-producer.json').write_text('{"generated": true}')
        elif a[0]=='bless' and '--label' in a:
            folder=Path(a[a.index('--folder')+1]);(folder/'.disk_label').write_text('new label');(folder/'.disk_label_2x').write_text('new label')
        return b''


class GoldenImageTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='golden-image-test-');self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        c=dict(image=str(self.root/'output.img'),work=str(self.root/'work'),size_bytes=32768,name='Golden',
               python='/staged/python',layout_helper='/staged/layout.py',pristine_script='/staged/pristine.sh',
               payload='/staged/golden-payload',kernel='/staged/kernel',kdk='/staged/kdk',kdk_preparer='/staged/kdk-preparer.py',
               fl='/staged/fl',donor_tool='/staged/tahoe-donors/make',donors={},esp=str(self.root/'esp'),
               hardware=dict(tool='/staged/hardware/apply',artifact='/staged/hardware'),kernel_hook='/staged/kernel/kc-hook.sh',
               userspace=dict(install='/staged/userspace/install.py',shim='/staged/display.dylib',io80211='/staged/io80211.dylib'),
               hooks=[dict(name='selectors',hook='/staged/selectors/hook.sh',kc_hook=None),
                      dict(name='hao',hook='/staged/hao/hook.sh',kc_hook='/staged/hao/kc-hook.sh'),
                      dict(name='metal',hook=None,kc_hook='/staged/metal/kc-hook.sh')],
               posts=[dict(name='esp',script='/staged/posts/esp/apply.sh',folder='/staged/posts/esp')],
               environment=dict(PYTHONPATH='/staged/site',OCLP='/staged/oclp'))
        for key in ('native','extensions','driverkit','boot','recovery'):
            p=self.root/key;c['donors'][key]=str(p)
            role='preboot' if key=='boot' else key
            source=p/'payload'/role;source.mkdir(parents=True);(source/'fixture').write_text(key+' source')
        (self.root/'esp').mkdir();(self.root/'esp/boot.efi').write_text('authored ESP')
        self.config=c;self.fake=FakeCommands(c)

    def commands(self):return [row['command'] for row in self.fake.entries]

    def test_full_order_preserves_golden_identity_and_declared_roles(self):
        record=golden.assemble(self.config,self.fake);a=self.commands()
        self.assertIn(['/sbin/mount_msdos','-o','nobrowse','/dev/disk70s1',str(Path(self.config['work'])/'mnt/esp')],a)
        self.assertFalse(any(x[:2]==['diskutil','mount'] and x[-1]=='/dev/disk70s1' for x in a))
        def at(predicate):return next(i for i,row in enumerate(a) if predicate(row))
        system=at(lambda x:x[:3]==['sh',self.config['pristine_script'],'system'])
        data=at(lambda x:x[:3]==['diskutil','apfs','addVolume'] and 'D' in x)
        group=at(lambda x:x[:4]==[self.config['python'],'-I',self.config['layout_helper'],'group'])
        pb=at(lambda x:x[:3]==['diskutil','apfs','addVolume'] and 'B' in x)
        self.assertLess(system,data);self.assertLess(data,group);self.assertLess(group,pb)
        finalize=at(lambda x:x[:3]==['sh',self.config['pristine_script'],'finalize'])
        hardware=at(lambda x:self.config['hardware']['tool'] in x)
        self.assertEqual(a[finalize][a[finalize].index('--native-artifact')+1],self.config['donors']['native'])
        self.assertLess(finalize,hardware)  # Whole Tahoe framework bases precede PSP merges.
        kernel=at(lambda x:self.config['kernel_hook'] in x)
        userspace=at(lambda x:self.config['userspace']['install'] in x)
        before=[at(lambda x,p=h['hook']:p in x) for h in self.config['hooks'] if h['hook']]
        after=[at(lambda x,p=h['kc_hook']:p in x) for h in self.config['hooks'] if h['kc_hook']]
        self.assertTrue(all(hardware<i<kernel for i in before));self.assertLess(kernel,userspace)
        self.assertTrue(all(userspace<i for i in after));self.assertEqual(after,sorted(after))
        self.assertIn('--prepared-system',a[userspace]);self.assertIn(self.config['donors']['driverkit'],a[userspace])
        post=next(row for row in self.fake.entries if self.config['posts'][0]['script'] in row['command'])
        self.assertEqual(post['environment']['GRP'],'DATA-UUID');self.assertEqual(post['environment']['P'],'/staged/posts/esp')
        self.assertEqual(post['environment']['SYSU'],'SYSTEM-UUID');self.assertEqual(post['environment']['OCLP_PYTHON'],'/staged/python')
        self.assertEqual(record['status'],'assembled');self.assertIsNone(record['attached_device'])
        work=Path(self.config['work']);core=work/'mnt/preboot/DATA-UUID/System/Library/CoreServices'
        self.assertEqual(post['environment']['PATCH_OUTPUT_DIR'],str(work/'post/esp'))
        self.assertTrue(json.loads((work/'post/esp/fixture-producer.json').read_text())['generated'])
        self.assertEqual((work/'BootKernelExtensions.kc').read_bytes(),b'authored collection')
        self.assertEqual((core/'.root_uuid').read_text(),'DATA-UUID')
        self.assertEqual(plistlib.loads((core/'SystemVersion.plist').read_bytes())['ProductVersion'],'27.0')
        self.assertFalse((work/'mnt/system/System/Library/CoreServices/.disk_label').exists())
        self.assertTrue((work/'mnt/preboot/DATA-UUID/boot/System/Library/KernelCollections').is_dir())
        self.assertTrue((work/'mnt/system/System/Library/KernelCollections').is_dir())
        self.assertIn('GRP=DATA-UUID',(work/'uuids.env').read_text())
        self.assertEqual(a[-1],['hdiutil','detach','/dev/disk70'])

    def test_hardware_failure_stops_required_later_components_and_detaches(self):
        self.fake.fail=lambda a:self.config['hardware']['tool'] in a
        with self.assertRaises(subprocess.CalledProcessError):golden.assemble(self.config,self.fake)
        self.assertFalse(any(self.config['kernel_hook'] in a for a in self.commands()))
        record=json.loads((Path(self.config['work'])/'build.json').read_text())
        self.assertEqual(record['status'],'failed');self.assertIsNone(record['attached_device'])
        self.assertTrue(Path(self.config['image']).is_file())

    def test_unmount_failure_retains_attached_image_and_fails_completion(self):
        data=str(Path(self.config['work'])/'mnt/data')
        self.fake.fail=lambda a:a==['diskutil','unmount',data]
        with self.assertRaises(RuntimeError):golden.assemble(self.config,self.fake)
        record=json.loads((Path(self.config['work'])/'build.json').read_text())
        self.assertEqual(record['status'],'failed');self.assertEqual(record['attached_device'],'/dev/disk70')
        self.assertFalse(any(a[:2]==['hdiutil','detach'] for a in self.commands()))

    def test_hook_without_scripts_is_not_silently_skipped(self):
        self.config['hooks']=[dict(name='required-fix',hook=None,kc_hook=None)]
        with self.assertRaisesRegex(ValueError,'no script'):golden.assemble(self.config,self.fake)
        self.assertFalse(any(self.config['kernel_hook'] in a for a in self.commands()))

    def test_missing_recovery_role_does_not_report_complete_image(self):
        del self.config['donors']['recovery']
        with self.assertRaises(KeyError):golden.assemble(self.config,self.fake)
        record=json.loads((Path(self.config['work'])/'build.json').read_text())
        self.assertEqual(record['status'],'failed')

    def test_update_preboot_mount_displacement_is_repaired_before_metadata_write(self):
        self.fake.displace_preboot=True
        record=golden.assemble(self.config,self.fake);a=self.commands()
        update=next(i for i,x in enumerate(a) if x[:3]==['diskutil','apfs','updatePreboot'])
        remount=next(i for i,x in enumerate(a) if i>update and x[:2]==['diskutil','mount'] and x[-1]==record['volumes']['preboot'])
        version=next(i for i,x in enumerate(a) if i>update and x[0]=='ditto')
        self.assertLess(remount,version)
        self.assertIn(['diskutil','unmount',record['volumes']['preboot']],a[update:remount])
        snapshot=next(i for i,x in enumerate(a) if x[:2]==['bless','--mount'])
        pre=str(Path(self.config['work'])/'mnt/preboot')
        self.assertIn(['diskutil','unmount',pre],a[version:snapshot])
        self.assertTrue(any(i>snapshot and x[:2]==['diskutil','mount'] and x[-1]==record['volumes']['preboot'] for i,x in enumerate(a)))
        self.assertEqual(record['status'],'assembled');self.assertIsNone(record['attached_device'])

    def test_existing_image_is_not_overwritten_or_attached(self):
        image=Path(self.config['image']);image.write_bytes(b'preserved')
        with self.assertRaises(FileExistsError):golden.assemble(self.config,self.fake)
        self.assertEqual(image.read_bytes(),b'preserved');self.assertEqual(self.fake.entries,[])


if __name__=='__main__':unittest.main()
