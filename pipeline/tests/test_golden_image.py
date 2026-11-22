"""Guest assembly sequencing with regular files and fake commands; no APFS calls."""
import importlib.util
import json
from pathlib import Path
import plistlib
import shutil
import subprocess
import os
import tempfile
import unittest

SCRIPT=Path(__file__).resolve().parents[1]/'stages/golden-image.py'
spec=importlib.util.spec_from_file_location('golden_image',SCRIPT)
golden=importlib.util.module_from_spec(spec);spec.loader.exec_module(golden)


class FakeCommands:
    def __init__(self,config,fail=None):
        self.config=config;self.fail=fail;self.entries=[];self.volumes=[];self.grouped=False;self.displace_preboot=False;self.esp=None

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
                if row['DeviceIdentifier']==a[-1]:
                    point=a[a.index('-mountPoint')+1]
                    if row.get('files') and row['files']!=point:   # the volume's files, mounted again elsewhere (a resume)
                        shutil.copytree(row['files'],point,symlinks=True,dirs_exist_ok=True)
                    row['MountPoint']=row['files']=point
        elif a[0]=='/sbin/mount_msdos':      # the ESP's files, mounted again elsewhere (a resume)
            if self.esp and self.esp!=a[-1]:shutil.copytree(self.esp,a[-1],dirs_exist_ok=True)
            self.esp=a[-1]
        elif a[:2]==['diskutil','enableOwnership'] and Path(a[-1]).name=='esp':
            raise AssertionError('FAT EFI volume does not support Unix ownership')
        elif a[:2]==['diskutil','unmount']:
            for row in self.volumes:
                if a[-1] in (row['DeviceIdentifier'],row.get('MountPoint')):row['MountPoint']=''
        elif a[:3]==['diskutil','apfs','updatePreboot'] and self.displace_preboot:
            for row in self.volumes:
                if row['Roles']==['Preboot']:row['MountPoint']='/Volumes/UnexpectedPreboot'
        elif a[:3]==['diskutil','info','-plist']:
            row=next((row for row in self.volumes if a[3] in (row['DeviceIdentifier'],row['VolumeUUID'])),None)
            if row is None:raise subprocess.CalledProcessError(1,a)    # diskutil: no such disk or volume
            return plistlib.dumps({k:v for k,v in row.items() if k!='files'})
        elif a[:2]==['rm','-rf']:
            shutil.rmtree(a[2],ignore_errors=True)
        elif a[0]==self.config['python'] and a[2]==self.config['layout_helper']:
            assert a[3]=='group'
            assert len(self.volumes)==2 and a[-1]==self.volumes[1]['DeviceIdentifier']
            assert not any(x['command'][:2]==['diskutil','mount'] and x['command'][-1]==a[-1] for x in self.entries)
            self.grouped=True
            return json.dumps({'group_uuid':'DATA-UUID'}).encode()
        elif a[0]=='sh' and a[1]==self.config['pristine_script']:
            system=Path(self.config['work'])/'mnt/system'
            if a[2]=='system':
                (Path(self.config['work'])/'set-id.json').write_text('[]')
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
        elif a[:3]==['sh','-eu',self.config['hooks'][0]['kc_hook']]:
            kc=Path(a[3])/'System/Library/KernelCollections';kc.mkdir(parents=True,exist_ok=True)
            (kc/'BootKernelExtensions.kc').write_bytes(b'authored collection')
            (kc/'BootKernelExtensions.kc.elides').write_text('com.apple.iokit.IOPCIFamily\n')
            (kc/'SystemKernelExtensions.kc').write_bytes(b'authored system collection')
        elif a[:2]==['sh','-eu'] and any(a[2]==h['kc_hook'] for h in self.config['hooks'][1:]):
            # A later recipe changes the collection; the record keeps darwin27-kernel's bytes.
            (Path(a[3])/'System/Library/KernelCollections/BootKernelExtensions.kc').write_bytes(b'edited by '+a[2].encode())
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
               hardware=dict(tool='/staged/hardware/apply',artifact='/staged/hardware'),
               hooks=[dict(name='darwin27-kernel',kc_hook='/staged/kernel/kc-hook.sh'),
                      dict(name='golden-gate-userspace',kc_hook='/staged/userspace/kc-hook.sh'),
                      dict(name='hao',kc_hook='/staged/hao/kc-hook.sh'),
                      dict(name='selectors',kc_hook='/staged/selectors/kc-hook.sh'),
                      dict(name='metal',kc_hook='/staged/metal/kc-hook.sh')],
               posts=[dict(name='esp',script='/staged/posts/esp/apply.sh',folder='/staged/posts/esp')],
               environment=dict(PYTHONPATH='/staged/site',OCLP='/staged/oclp'))
        for key in ('native','extensions','driverkit','boot','recovery'):
            p=self.root/key;c['donors'][key]=str(p)
            role='preboot' if key=='boot' else key
            source=p/'payload'/role;source.mkdir(parents=True);(source/'fixture').write_text(key+' source')
        (self.root/'esp').mkdir();(self.root/'esp/boot.efi').write_text('authored ESP')
        config=self.root/'esp/EFI/OC/config.plist';config.parent.mkdir(parents=True)
        config.write_bytes(plistlib.dumps({'#Revision':{'Original-Model':'FixtureModel','Hardware-Probe':b'private probe'}}))
        self.config=c;self.fake=FakeCommands(c)

    def commands(self):return [row['command'] for row in self.fake.entries]

    def test_final_esp_reasons_checked_once_after_the_last_post(self):
        self.config['posts'].append(dict(name='hao-opencore',script='/staged/posts/oc/apply.sh',folder='/staged/posts/oc'))
        self.config['posts'].append(dict(name='later',script='/staged/posts/later/apply.sh',folder='/staged/posts/later'))
        golden.assemble(self.config,self.fake);a=self.commands()
        checks=[i for i,x in enumerate(a) if '/staged/posts/oc/src/config_reasons.py' in x]
        self.assertEqual(len(checks),1)
        x=a[checks[0]];payload=str(Path(self.config['work'])/'boot-efi/payload')
        self.assertEqual(x[:5],['/staged/python','-I','-B','/staged/posts/oc/src/config_reasons.py','check'])
        self.assertEqual(x[5:],[payload+'/EFI/OC/config.plist','--kexts',payload+'/EFI/OC/Kexts'])
        later=next(i for i,y in enumerate(a) if '/staged/posts/later/apply.sh' in y)
        self.assertLess(later,checks[0])

    def test_no_reasons_check_without_hao_opencore(self):
        golden.assemble(self.config,self.fake)
        self.assertFalse(any('config_reasons.py' in ' '.join(map(str,x)) for x in self.commands()))

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
        recipes=[at(lambda x,p=h['kc_hook']:p in x) for h in self.config['hooks']]
        self.assertLess(hardware,recipes[0]);self.assertEqual(recipes,sorted(recipes))  # one loop, set order
        for h in self.config['hooks']:
            self.assertEqual(a[at(lambda x,p=h['kc_hook']:p in x)],['sh','-eu',h['kc_hook'],str(Path(self.config['work'])/'mnt/system')])
        userspace=next(row for row in self.fake.entries if self.config['hooks'][1]['kc_hook'] in row['command'])
        self.assertEqual(userspace['environment']['TAHOE_DRIVERKIT_ARTIFACT'],self.config['donors']['driverkit'])
        self.assertEqual(userspace['environment']['TAHOE_DONOR_TOOL'],self.config['donor_tool'])
        self.assertEqual(userspace['environment']['GRP'],'DATA-UUID')
        steps=[row['name'] for row in record['steps']]
        self.assertEqual([n for n in steps if not n.startswith(('recipe ','post '))],[n for n,_,_ in golden.LAYERS])
        self.assertEqual([n[7:] for n in steps if n.startswith('recipe ')],[h['name'] for h in self.config['hooks']])
        post=next(row for row in self.fake.entries if self.config['posts'][0]['script'] in row['command'])
        self.assertEqual(post['environment']['GRP'],'DATA-UUID');self.assertEqual(post['environment']['P'],'/staged/posts/esp')
        self.assertEqual(post['environment']['SYSU'],'SYSTEM-UUID');self.assertEqual(post['environment']['OCLP_PYTHON'],'/staged/python')
        self.assertEqual(record['status'],'assembled');self.assertIsNone(record['attached_device'])
        work=Path(self.config['work']);core=work/'mnt/preboot/DATA-UUID/System/Library/CoreServices'
        self.assertEqual(post['environment']['PATCH_OUTPUT_DIR'],str(work/'post/esp'))
        self.assertTrue(json.loads((work/'post/esp/fixture-producer.json').read_text())['generated'])
        self.assertEqual((work/'BootKernelExtensions.kc').read_bytes(),b'authored collection')
        efi=json.loads((work/'boot-efi/manifest.json').read_text())
        self.assertEqual(efi['model'],'FixtureModel')
        self.assertEqual(efi['os_build'],'26A428')
        self.assertNotIn('Hardware-Probe',efi['opencore_revision'])
        self.assertFalse(efi['kernel_repairs_applied'])
        self.assertEqual((work/'boot-efi/payload/boot.efi').read_text(),'authored ESP')
        self.assertEqual(efi['files']['boot.efi'],__import__('hashlib').sha256(b'authored ESP').hexdigest())

        self.assertEqual((core/'.root_uuid').read_text(),'DATA-UUID')
        self.assertEqual(plistlib.loads((core/'SystemVersion.plist').read_bytes())['ProductVersion'],'27.0')
        self.assertFalse((work/'mnt/system/System/Library/CoreServices/.disk_label').exists())
        self.assertIn('GRP=DATA-UUID',(work/'uuids.env').read_text())
        self.assertEqual(a[-1],['hdiutil','detach','/dev/disk70'])

    def test_every_hook_and_post_sees_the_target(self):
        # golden-run puts the target in the configuration's environment (target_environment)
        self.config['environment'].update(HAO_TARGET_MODEL='MacBookPro9,2',HAO_TARGET_DATA='/kit/target-data.json')
        golden.assemble(self.config,self.fake)
        scripts=[h['kc_hook'] for h in self.config['hooks']]+[p['script'] for p in self.config['posts']]
        for script in scripts:
            row=next(r for r in self.fake.entries if script in r['command'])
            self.assertEqual(row['environment']['HAO_TARGET_MODEL'],'MacBookPro9,2',script)
            self.assertEqual(row['environment']['HAO_TARGET_DATA'],'/kit/target-data.json',script)

    def test_overlay_plan_replaces_those_hooks_at_their_turn(self):
        # golden-run --from-overlays: selectors and the esp post install from the overlays, every other recipe runs
        plan=self.root/'overlay-plan.json'
        modes={'darwin27-kernel':'hook','golden-gate-userspace':'hook','hao':'hook','selectors':'overlay',
               'metal':'hook','esp':'overlay'}
        plan.write_text(json.dumps({'format':1,'steps':[dict(name=n,mode=m,files=[]) for n,m in modes.items()]}))
        self.config['overlays']=dict(plan=str(plan),files='/share/overlay-files',installer='/staged/golden-overlays.py',
                                     livepatch='/staged/livepatch.py')
        record=golden.assemble(self.config,self.fake);a=self.commands()
        installs=[x for x in a if x[:3]==[self.config['python'],'-I','/staged/golden-overlays.py']]
        self.assertEqual([x[x.index('--recipe')+1] for x in installs],['selectors','esp'])
        work=Path(self.config['work'])
        self.assertEqual(installs[0][3:6],['install',str(plan),'/share/overlay-files'])
        self.assertIn('system='+str(work/'mnt/system'),installs[0]);self.assertIn('data='+str(work/'mnt/data'),installs[0])
        self.assertIn('esp='+str(work/'mnt/esp'),installs[1])
        self.assertEqual(installs[0][installs[0].index('--record')+1],str(work/'overlays/selectors.json'))
        hooks={h['name']:h['kc_hook'] for h in self.config['hooks']}
        self.assertFalse(any(hooks['selectors'] in x for x in a))                    # its hook does not run
        self.assertFalse(any(self.config['posts'][0]['script'] in x for x in a))      # nor the post's apply.sh
        at=lambda p:next(i for i,x in enumerate(a) if p(x))
        selectors=at(lambda x:x in installs[:1])
        self.assertLess(at(lambda x:hooks['hao'] in x),selectors);self.assertLess(selectors,at(lambda x:hooks['metal'] in x))
        self.assertLess(at(lambda x:x[:2]==['bless','--mount']),at(lambda x:x in installs[1:]))   # posts: after the snapshot
        steps=[row['name'] for row in record['steps']]
        self.assertIn('overlay selectors',steps);self.assertIn('overlay esp',steps);self.assertNotIn('recipe selectors',steps)
        self.assertEqual(record['status'],'assembled')

    def test_hardware_failure_stops_required_later_components_and_detaches(self):
        self.fake.fail=lambda a:self.config['hardware']['tool'] in a
        with self.assertRaises(subprocess.CalledProcessError):golden.assemble(self.config,self.fake)
        self.assertFalse(any(h['kc_hook'] in a for h in self.config['hooks'] for a in self.commands()))
        record=json.loads((Path(self.config['work'])/'build.json').read_text())
        self.assertEqual(record['status'],'failed');self.assertIsNone(record['attached_device'])
        self.assertTrue(Path(self.config['image']).is_file())

    def test_no_hardware_overlay_runs_nothing_and_says_so(self):
        # golden-run's config for a target that selects no OCLP patch sets (the generic one): hardware null
        self.config['hardware']=None
        record=golden.assemble(self.config,self.fake)
        self.assertEqual(record['status'],'assembled')
        self.assertFalse(any('/staged/hardware' in x for a in self.commands() for x in a))
        self.assertIn('hardware',[row['name'] for row in record['steps']])
        self.assertIsNone(json.loads((Path(self.config['work'])/'hardware.json').read_text())['overlay'])
        self.assertNotIn('/staged/hardware',golden.source_paths(self.config))

    def test_unmount_failure_retains_attached_image_and_fails_completion(self):
        data=str(Path(self.config['work'])/'mnt/data')
        self.fake.fail=lambda a:a==['diskutil','unmount',data]
        with self.assertRaises(RuntimeError):golden.assemble(self.config,self.fake)
        record=json.loads((Path(self.config['work'])/'build.json').read_text())
        self.assertEqual(record['status'],'failed');self.assertEqual(record['attached_device'],'/dev/disk70')
        self.assertFalse(any(a[:2]==['hdiutil','detach'] for a in self.commands()))

    def test_recipe_without_kc_hook_is_not_silently_skipped(self):
        self.config['hooks'].append(dict(name='required-fix',kc_hook=None))
        with self.assertRaisesRegex(ValueError,'no src/kc-hook.sh: required-fix'):golden.assemble(self.config,self.fake)
        self.assertFalse(any(h['kc_hook'] and h['kc_hook'] in a for h in self.config['hooks'] for a in self.commands()))

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

    def test_snapshot_bless_takes_the_intel_form_on_x86_64(self):
        system=Path('/w/mnt/system')
        self.assertEqual(golden.snapshot_command(system,'arm64'),('bless','--mount',system,'--create-snapshot'))
        self.assertEqual(golden.snapshot_command(system,'x86_64'),
                         ('bless','--folder',system/'System/Library/CoreServices','--bootefi','--create-snapshot'))

    def test_existing_image_is_not_overwritten_or_attached(self):
        image=Path(self.config['image']);image.write_bytes(b'preserved')
        with self.assertRaises(FileExistsError):golden.assemble(self.config,self.fake)
        self.assertEqual(image.read_bytes(),b'preserved');self.assertEqual(self.fake.entries,[])


if __name__=='__main__':unittest.main()


class ResumeTests(unittest.TestCase):
    """golden-image.py --resume: a failed run continues at a RESUMABLE step on the same disk (bench2 rerun3 stopped
    at boot-and-esp 90 minutes in)."""
    def setUp(self):
        base=GoldenImageTests('test_full_order_preserves_golden_identity_and_declared_roles');base.setUp()
        self.addCleanup(base.temp.cleanup);self.root=base.root
        self.first=dict(base.config,work=str(self.root/'a/work'),image=str(self.root/'a/target.img'),
                        environment=dict(base.config['environment'],LOG=str(self.root/'a/log')))
        self.fake=FakeCommands(self.first)

    def fail_at(self,predicate):
        self.fake.fail=predicate
        with self.assertRaises(subprocess.CalledProcessError):golden.assemble(self.first,self.fake)
        self.fake.fail=None
        prior=json.loads((Path(self.first['work'])/'build.json').read_text());prior['work']=self.first['work']
        return prior

    def second(self):
        # another LOCAL (b/): the paths below it differ, the image is the failed run's
        c=dict(self.first,work=str(self.root/'b/work'),image=str(self.root/'b/target.img'),
               environment=dict(self.first['environment'],LOG=str(self.root/'b/log')))
        self.fake.config=c;self.fake.entries=[]
        return c

    def test_interrupted_record_write_keeps_previous_checkpoint(self):
        from unittest.mock import patch
        work=self.root/'checkpoint';work.mkdir()
        golden.write_build_record(work,{'status':'failed','step':'cache-build'})
        with patch.object(golden.os,'replace',side_effect=OSError('interrupted')):
            with self.assertRaises(OSError):golden.write_build_record(work,{'status':'assembled'})
        self.assertEqual(json.loads((work/'build.json').read_text()),{'status':'failed','step':'cache-build'})

    def cache_setup(self):
        hook=dict(name='cache-fixture',kc_hook='/staged/cache/kc-hook.sh')
        self.first['hooks'].insert(2,hook)
        self.first['cache_rebuild_hook']=hook['name']
        return hook

    def test_cache_build_resumes_without_repeating_source_recipes(self):
        hook=self.cache_setup()
        prior=self.fail_at(lambda a:hook['kc_hook'] in a)
        c=self.second()
        self.assertEqual(golden.LAYERS[golden.resume_point(c,prior)][0],'cache-build')
        record=golden.assemble(c,self.fake,prior=prior)
        calls=[r for r in self.fake.entries if hook['kc_hook'] in r['command']]
        self.assertEqual([r['environment']['HAO_CACHE_PHASE'] for r in calls],['build','install'])
        self.assertFalse(any(self.first['hooks'][1]['kc_hook'] in r['command'] for r in self.fake.entries))
        self.assertEqual(record['status'],'assembled')

    def test_cache_resume_preserves_set_id_gate(self):
        hook=self.cache_setup();prior=self.fail_at(lambda a:hook['kc_hook'] in a)
        source=Path(self.first['work'])/'mnt/system/System/Library/KernelCollections/BootKernelExtensions.kc'
        st=source.stat()
        expected=[['System/Library/KernelCollections/BootKernelExtensions.kc',0o4755,st.st_uid,st.st_gid]]
        (Path(prior['work'])/'set-id.json').write_text(json.dumps(expected))
        c=self.second()
        with self.assertRaisesRegex(RuntimeError,'not sealed'):golden.assemble(c,self.fake,prior=prior)
        self.assertEqual(json.loads((Path(c['work'])/'set-id.json').read_text()),expected)
        self.assertFalse(any('--create-snapshot' in row['command'] for row in self.fake.entries))

    def test_cache_mutation_checkpoint_refuses_resume(self):
        hook=self.cache_setup(); original=self.fake.run
        checkpoint=[]
        def run(args, **kwargs):
            if hook['kc_hook'] in [str(a) for a in args]:
                checkpoint.append(json.loads((Path(self.first['work'])/'build.json').read_text()))
                if kwargs.get('env',{}).get('HAO_CACHE_PHASE')=='install':
                    raise subprocess.CalledProcessError(1,args)
            return original(args,**kwargs)
        self.fake.run=run
        with self.assertRaises(subprocess.CalledProcessError): golden.assemble(self.first,self.fake)
        self.assertEqual(len(checkpoint),2)
        for record,phase in zip(checkpoint,('cache-build','cache-install')):
            incomplete=[r['name'] for r in record['steps'] if not r.get('complete')]
            self.assertEqual(incomplete,[phase])
        prior=checkpoint[-1];prior['work']=self.first['work']
        with self.assertRaisesRegex(ValueError,'cache-install'):golden.resume_point(self.second(),prior)

    def test_resumes_at_boot_and_esp_without_erasing(self):
        prior=self.fail_at(lambda a:a[0]=='ditto' and a[-1].endswith('/mnt/esp'))
        self.assertEqual(prior['steps'][-1]['name'],'boot-and-esp');self.assertNotIn('complete',prior['steps'][-1])
        c=self.second()
        self.assertEqual(golden.LAYERS[golden.resume_point(c,prior)][0],'boot-and-esp')
        record=golden.assemble(c,self.fake,prior=prior);a=[r['command'] for r in self.fake.entries]
        self.assertEqual(record['status'],'assembled')
        self.assertEqual(record['resumed']['step'],'boot-and-esp')
        self.assertEqual(record['configuration']['image'],self.first['image'])     # the failed run's disk
        for erased in (['diskutil','partitionDisk'],['diskutil','apfs','addVolume'],['diskutil','apfs','deleteVolume']):
            self.assertFalse(any(x[:len(erased)]==erased for x in a),erased)
        self.assertFalse(any(x[:2]==['sh',c['pristine_script']] for x in a))       # golden-system, golden-finalize: kept
        self.assertIn(['hdiutil','attach','-imagekey','diskimage-class=CRawDiskImage','-nomount','-plist',self.first['image']],a)
        self.assertIn([*golden.FAT_COPY,c['esp'],str(Path(c['work'])/'mnt/esp')],a)
        self.assertTrue(any(x[:2]==['rm','-rf'] and x[2].endswith('mnt/preboot/DATA-UUID') for x in a))
        self.assertIn('GRP=DATA-UUID',(Path(c['work'])/'uuids.env').read_text())
        steps=[(r['name'],r.get('reused',False)) for r in record['steps'] if r['name'] in {n for n,_,_ in golden.LAYERS}]
        self.assertEqual(steps,[(n,i<4) for i,(n,_,_) in enumerate(golden.LAYERS)])
        hook=next(r for r in self.fake.entries if c['hooks'][1]['kc_hook'] in r['command'])
        self.assertEqual(hook['environment']['SYSU'],'SYSTEM-UUID');self.assertEqual(hook['environment']['GRP'],'DATA-UUID')

    def test_the_disk_may_have_another_name_after_a_restart(self):
        prior=self.fail_at(lambda a:a[0]=='ditto' and a[-1].endswith('/mnt/esp'))
        prior['configuration'].update(disk='disk0',confirm_disk='disk0')
        c=dict(self.second(),disk='disk1',confirm_disk='disk1')
        self.assertEqual(golden.LAYERS[golden.resume_point(c,prior)][0],'boot-and-esp')

    def test_refuses_a_disk_without_the_failed_runs_volumes(self):
        prior=self.fail_at(lambda a:a[0]=='ditto' and a[-1].endswith('/mnt/esp'))
        c=self.second()
        for row in self.fake.volumes:
            row['VolumeUUID']='OTHER-'+row['VolumeUUID']     # another disk: none of the failed run's volumes
        with self.assertRaisesRegex(ValueError,'not the failed run'):golden.assemble(c,self.fake,prior=prior)

    def test_refuses_a_step_that_edits_in_place(self):
        prior=self.fail_at(lambda a:self.first['hooks'][2]['kc_hook'] in a)
        with self.assertRaisesRegex(ValueError,'stopped in step recipes'):golden.resume_point(self.second(),prior)

    def system_unmount(self,a):
        return a[:2]==['diskutil','unmount'] and str(a[-1]).endswith('mnt/system')

    def test_a_held_volume_is_unmounted_again_then_forced(self):
        old=golden.UNMOUNT_WAIT;golden.UNMOUNT_WAIT=0;self.addCleanup(setattr,golden,'UNMOUNT_WAIT',old)
        refused=[]    # held through every plain attempt of the first unmount (posts'), then free
        def fail(a):
            if self.system_unmount(a) and 'force' not in a and len(refused)<golden.UNMOUNT_TRIES:
                refused.append(a);return True
        self.fake.fail=fail
        record=golden.assemble(self.first,self.fake);a=[r['command'] for r in self.fake.entries]
        self.assertEqual(record['status'],'assembled')
        forced=[i for i,x in enumerate(a) if x[:3]==['diskutil','unmount','force']]
        self.assertEqual(len(forced),1)
        self.assertEqual(sum(1 for x in a[:forced[0]] if self.system_unmount(x) and 'force' not in x),golden.UNMOUNT_TRIES)

    def test_resumes_at_posts_when_no_post_started(self):
        old=golden.UNMOUNT_WAIT;golden.UNMOUNT_WAIT=0;self.addCleanup(setattr,golden,'UNMOUNT_WAIT',old)
        prior=self.fail_at(self.system_unmount)
        self.assertEqual([r['name'] for r in prior['steps'] if not r.get('complete')][0],'posts')
        c=self.second();self.assertEqual(golden.LAYERS[golden.resume_point(c,prior)][0],'posts')
        record=golden.assemble(c,self.fake,prior=prior);a=[r['command'] for r in self.fake.entries]
        self.assertEqual(record['status'],'assembled');self.assertEqual(record['resumed']['step'],'posts')
        self.assertFalse(any(x[:2]==['sh',c['pristine_script']] or x[:1]==['bless'] for x in a))

    def test_refuses_posts_after_a_post_started(self):
        script=self.first['posts'][0]['script']
        prior=self.fail_at(lambda a:script in a)
        with self.assertRaisesRegex(ValueError,'stopped in step posts after'):golden.resume_point(self.second(),prior)

    def test_refuses_another_kit(self):
        prior=self.fail_at(lambda a:self.first['hardware']['tool'] in a)
        c=self.second();self.assertEqual(golden.LAYERS[golden.resume_point(c,prior)][0],'hardware')
        c['hooks']=c['hooks'][:-1]
        with self.assertRaisesRegex(ValueError,'configuration differs.*hooks'):golden.resume_point(c,prior)

    def test_refuses_a_run_that_did_not_fail(self):
        golden.assemble(self.first,self.fake)
        prior=json.loads((Path(self.first['work'])/'build.json').read_text());prior['work']=self.first['work']
        with self.assertRaisesRegex(ValueError,'did not fail'):golden.resume_point(self.second(),prior)


class FatCopyTests(unittest.TestCase):
    def test_every_copy_onto_the_esp_is_data_only(self):
        """The assembly and every post write the ESP without xattrs/ACLs/resource forks (no doubleagentd in a Recovery)."""
        text=SCRIPT.read_text()
        self.assertNotIn("'--extattr'",text);self.assertNotIn("'--acl'",text)
        esp=[l for l in text.splitlines() if 'self.run(' in l and "mounts['esp']" in l and 'mount_msdos' not in l]
        self.assertEqual(len(esp),2)   # bootstrap's copy onto it, export_efi's copy from it
        self.assertTrue(all('FAT_COPY' in l for l in esp),esp)
        top=SCRIPT.parents[2]
        for script in sorted(top.glob('patches/*/apply.sh'))+sorted(top.glob('patches/*/src/*.sh')):
            for n,line in enumerate(script.read_text().splitlines(),1):
                if line.lstrip().startswith('#') or not ('ditto' in line or line.lstrip().startswith(('cp ','mv '))):continue
                if '"$ESP' in line.split()[-1] if line.split() else False:
                    self.assertIn('--noextattr --noacl',line,'%s:%d copies onto the FAT ESP with attributes' % (script.relative_to(top),n))


class UnknownOwners(unittest.TestCase):
    """A Recovery reads the stick noowners: files the kit gives the target arrive as _unknown (99:99) and kmutil
    refuses such kexts. fixed_owners gives them root/wheel, on the volume's own device, symlinks themselves."""
    def test_only_unknown_ids_change(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)/'vol';(root/'Ext/A.kext/Contents').mkdir(parents=True)
            for name in ('Ext/A.kext/Contents/Info.plist','Ext/B','Ext/C'):(root/name).write_text('x')
            (root/'link').symlink_to('/elsewhere')
            owners={str(root/'Ext/A.kext'):(99,99),str(root/'Ext/A.kext/Contents/Info.plist'):(99,99),
                    str(root/'Ext/B'):(99,80),str(root/'Ext/C'):(501,99),str(root/'link'):(99,0)}
            real=os.lstat
            class St:
                def __init__(self,path):
                    st=real(path);self.st_dev=st.st_dev;self.st_uid,self.st_gid=owners.get(str(path),(0,0))
            calls=[]
            n=golden.fixed_owners(root,lstat=St,lchown=lambda p,u,g:calls.append((Path(p).relative_to(root).as_posix(),u,g)))
            self.assertEqual(5,n)
            self.assertEqual(sorted(calls),[('Ext/A.kext',0,0),('Ext/A.kext/Contents/Info.plist',0,0),('Ext/B',0,-1),
                                            ('Ext/C',-1,0),('link',0,-1)])


class SetIDCheckTests(unittest.TestCase):
    """diag#0057: the pre-seal check refuses a System whose set-ID files lost their mode or owner."""
    def test_lost_mode_is_named_and_removed_or_kept_files_pass(self):
        import os
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'usr/bin').mkdir(parents=True);uid,gid=os.getuid(),os.getgid()
            for name,mode in (('login',0o555),('sudo',0o4511)):
                (root/'usr/bin'/name).write_bytes(b'x');os.chmod(root/'usr/bin'/name,mode)
            expected=[['usr/bin/login',0o4555,uid,gid],['usr/bin/sudo',0o4511,uid,gid],['usr/bin/gone',0o4555,uid,gid]]
            bad=golden.check_set_id(expected,root)
            self.assertEqual(bad,['/usr/bin/login is 0555 %d:%d, not 4555 %d:%d'%(uid,gid,uid,gid)])
            os.chmod(root/'usr/bin/login',0o4555)
            self.assertEqual(golden.check_set_id(expected,root),[])
            self.assertTrue(golden.check_set_id([['usr/bin/sudo',0o4511,uid+1,gid]],root))


class ManifestTests(unittest.TestCase):
    """item 2050: golden-image writes one manifest per recipe after the seal; `hao verify` reads them and passes."""
    def test_manifests_verify_and_catch_a_change(self):
        import sys as _sys
        _sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools' / 'hao' / 'src'))
        import verify as V
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            system, data = root/'system', root/'data'
            (system/'usr/lib').mkdir(parents=True); (data/'private/var').mkdir(parents=True)
            (system/'usr/lib/a.dylib').write_bytes(b'a'); (system/'usr/lib/a.dylib').chmod(0o755)
            (data/'private/var/b.plist').write_bytes(b'b')
            declared = root/'declared.json'
            declared.write_text(json.dumps({'r1': {'pack': 'base', 'files': ['usr/lib/a.dylib', 'private/var/b.plist',
                                                                              'usr/lib/gone']}}))

            class A: pass
            a = A(); a.config = {'declared': str(declared)}; a.mounts = {'system': system, 'data': data}; a.manifests = {}
            golden.Assembler.write_manifests(a)
            self.assertEqual(a.manifests, {'r1': 2})
            m = json.loads((data/golden.MANIFESTS/'r1.json').read_text())
            self.assertEqual(m['files']['usr/lib/a.dylib']['mode'], '0755')
            # an installed Mac sees System and Data as one tree
            merged = root/'merged'
            shutil.copytree(system, merged); shutil.copytree(data, merged, dirs_exist_ok=True)
            found = V.manifests([], merged)
            self.assertEqual(V.verify(merged, found)['problems'], [])
            (merged/'usr/lib/a.dylib').write_bytes(b'changed')
            self.assertEqual([p['problem'] for p in V.verify(merged, V.manifests([], merged))['problems']], ['content'])

    def test_a_seed_file_carries_content_and_reason(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            system, data = root/'system', root/'data'
            system.mkdir(); (data/'Library/c').mkdir(parents=True)
            (data/'Library/c/functions.data').write_bytes(b'seed'); (data/'Library/c/plain').write_bytes(b'p')
            declared = root/'declared.json'
            declared.write_text(json.dumps({'r1': {'pack': 'base', 'files': ['Library/c/functions.data', 'Library/c/plain'],
                                                   'seed': {'Library/c/functions.data': 'Metal appends to it'}}}))

            class A: pass
            a = A(); a.config = {'declared': str(declared)}; a.mounts = {'system': system, 'data': data}; a.manifests = {}
            golden.Assembler.write_manifests(a)
            files = json.loads((data/golden.MANIFESTS/'r1.json').read_text())['files']
            self.assertEqual((files['Library/c/functions.data']['content'], files['Library/c/functions.data']['reason']),
                             ('seed', 'Metal appends to it'))
            self.assertEqual(len(files['Library/c/functions.data']['sha256']), 64)
            self.assertNotIn('content', files['Library/c/plain'])


class CryptexAllocationTests(unittest.TestCase):
    """#90: after the last writer, Preboot's cryptex images have no holes (the boot's APFS graft maps none)."""

    def holey(self, path, mib=1 << 20):
        with open(path, 'wb') as f:
            f.write(os.urandom(mib)); f.seek(64 * mib); f.write(b'\0' * mib); f.seek(128 * mib); f.write(os.urandom(mib))
            f.truncate(200 * mib)
        if not golden.hole_map(path):
            self.skipTest('this file system reports no holes (SEEK_HOLE)')

    def test_fills_holes_reclones_and_keeps_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            current = Path(tmp)
            self.holey(current/'os.dmg'); self.holey(current/'app.dmg')
            subprocess.run(['cp', '-c', str(current/'os.dmg'), str(current/'os.clone.dmg')], check=True)
            before = {n: (current/n).read_bytes() for n in ('os.dmg', 'app.dmg')}
            filled = golden.allocate_cryptex_images(current, run=lambda c: subprocess.run(c, check=True))
            self.assertEqual(set(filled), {'os.dmg', 'app.dmg'})
            for name in ('os.dmg', 'app.dmg', 'os.clone.dmg'):
                self.assertEqual(golden.hole_map(current/name), [], name)
            self.assertEqual({n: (current/n).read_bytes() for n in before}, before)
            self.assertEqual((current/'os.clone.dmg').read_bytes(), before['os.dmg'])
            self.assertEqual(golden.allocate_cryptex_images(current), {'os.dmg': 0, 'app.dmg': 0})

    def test_refuses_without_space_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            current = Path(tmp)
            self.holey(current/'os.dmg')
            holes = golden.hole_map(current/'os.dmg')
            with self.assertRaisesRegex(RuntimeError, 'nothing was written'):
                golden.allocate_cryptex_images(current, free=1 << 20)
            self.assertEqual(golden.hole_map(current/'os.dmg'), holes)

    def test_posts_run_it_after_the_last_post(self):
        text = SCRIPT.read_text()
        posts = text[text.index('    def posts(self):'):text.index('    def write_manifests(self):')]
        self.assertGreater(posts.index('allocate_cryptex_images'), posts.index("for post in self.config['posts']"))
        self.assertLess(posts.index('allocate_cryptex_images'), posts.index('self.write_manifests()'))
