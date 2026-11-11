import argparse
import errno
import os
import importlib.machinery
import importlib.util
import json
from pathlib import Path
import plistlib
import tempfile
import unittest
from unittest import mock
import shutil

UNIT=Path(__file__).resolve().parents[1]
loader=importlib.machinery.SourceFileLoader('tahoe_donors',str(UNIT/'make'))
spec=importlib.util.spec_from_loader(loader.name,loader)
tool=importlib.util.module_from_spec(spec);loader.exec_module(tool)


class Donors(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.tree=self.root/'tree';self.tree.mkdir()
        self.recipe=self.root/'recipe.json'
        self.recipe.write_text(json.dumps(dict(origin={'build':'fixture'},system_roots=['System/Donor'],build_tools=['usr/bin/kmutil'],required_image_roles=['system','build-tools','driverkit-cache','preboot'],preboot_exclude=['cryptex1/current/os.dmg'])))
        self.links=self.root/'links.txt';self.links.write_text('')
        self.meta=self.root/'sys.tsv';self.meta.write_text('System/Donor\tD\t0\t0\t755\t0\t12\t34\t-\nSystem/Donor/file\tF\t0\t80\t755\t20\t12\t34\tcom.example.test=6162\nSystem/Donor/link\tL\t0\t0\t755\t0\t12\t34\t-\nusr/bin/kmutil\tF\t0\t0\t755\t0\t12\t34\t-\n')

    def args(self,command,**kw):
        return argparse.Namespace(command=command,recipe=self.recipe,output=self.root/'out',tree=self.tree,metadata=None if command=='driverkit' else self.meta,links=self.links,**kw)

    def test_native_hash_links_and_authoritative_metadata_without_kernel(self):
        p=self.tree/'System/Donor';p.mkdir(parents=True);(p/'file').write_bytes(b'donor');(p/'link').symlink_to('file')
        p=self.tree/'usr/bin';p.mkdir(parents=True);(p/'kmutil').write_bytes(b'linker')
        p=self.tree/'System/Library/Kernels';p.mkdir(parents=True);(p/'kernel').write_bytes(b'excluded')
        manifest=tool.package(self.args('native'));rows={r['path']:r for r in manifest['entries']}
        self.assertEqual(rows['System/Donor/file']['sha256'],tool.sha(self.tree/'System/Donor/file'))
        self.assertEqual((rows['System/Donor/file']['uid'],rows['System/Donor/file']['gid'],rows['System/Donor/file']['mode']),(0,80,'755'))
        self.assertEqual(rows['System/Donor/file']['mtime_ns'],12000000034)
        self.assertEqual(rows['System/Donor/link']['link_target'],'file')
        self.assertFalse((self.root/'out/payload/system/System/Library/Kernels').exists())
        self.assertEqual((self.root/'out/payload/build-tools/usr/bin/kmutil').read_bytes(),b'linker')
        self.assertIn('driverkit-cache',manifest['required_image_roles'])
        self.assertEqual(json.loads((self.root/'out/build.json').read_text())['manifest_sha256'],tool.sha(self.root/'out/manifest.json'))

    def cache_recipe(self):
        p=self.tree/'System/Donor';p.mkdir(parents=True);(p/'file').write_bytes(b'donor');(p/'link').symlink_to('file')
        p=self.tree/'usr/bin';p.mkdir(parents=True);(p/'kmutil').write_bytes(b'linker')
        d=json.loads(self.recipe.read_text())
        d['cache_images']=[dict(path='System/Library/PrivateFrameworks/X.framework/Versions/A/X',family='dyld_shared_cache_x86_64h',
                                metadata=dict(uid=0,gid=0,mode='755',flags='80000'))]
        self.recipe.write_text(json.dumps(d))
        cache=self.root/'os-cache';cache.mkdir();(cache/'dyld_shared_cache_x86_64h').write_bytes(b'cache')
        return cache

    def test_cache_images_are_cut_out_into_the_system_role(self):
        cache=self.cache_recipe();calls=[]
        def extract(source,family,image,destination):
            calls.append((source,family,image));Path(destination).write_bytes(b'extracted')
            return {'binds':{'/usr/lib/libobjc.A.dylib':['_objc_msgSend']}},{'errors':[]}
        with mock.patch.object(tool,'extract_cache_image',side_effect=extract):
            manifest=tool.package(self.args('native',cache=cache))
        self.assertEqual(calls,[(cache,'dyld_shared_cache_x86_64h','/System/Library/PrivateFrameworks/X.framework/Versions/A/X')])
        row={r['path']:r for r in manifest['entries']}['System/Library/PrivateFrameworks/X.framework/Versions/A/X']
        self.assertEqual((row['role'],row['kind'],row['uid'],row['mode'],row['flags']),('system','file',0,'755','80000'))
        self.assertEqual(row['sha256'],tool.sha(self.root/'out/payload/system'/row['path']))
        self.assertEqual(row['mtime_ns'],(cache/'dyld_shared_cache_x86_64h').stat().st_mtime_ns)
        records=json.loads((self.root/'out/cache-images.json').read_text())
        self.assertEqual(records[row['path']]['main_sha256'],tool.sha(cache/'dyld_shared_cache_x86_64h'))
        self.assertIn(str((cache/'dyld_shared_cache_x86_64h').resolve()),[i['path'] for i in json.loads((self.root/'out/build.json').read_text())['inputs']])

    def test_cache_images_need_the_cache(self):
        self.cache_recipe()
        with self.assertRaisesRegex(ValueError,'give --cache'):
            tool.package(self.args('native'))

    def test_failed_cache_image_verification_stops(self):
        cache=self.cache_recipe()
        fake=mock.Mock(extract=lambda *a:{'stats':{}}); checker=mock.Mock(check=lambda *a:{'errors':['GOT slot 0x1 used by code is neither bound nor rebased']})
        with mock.patch.dict('sys.modules',{'cache_dylib':fake,'check':checker}),mock.patch.object(tool.subprocess,'run'):
            with self.assertRaisesRegex(ValueError,'fails verification'):
                tool.package(self.args('native',cache=cache))

    def test_intel_recipe_cuts_tahoe_corewifi_from_the_os_cache(self):
        recipe=json.loads((UNIT/'recipes/intel-donors.json').read_text())
        self.assertEqual([i['path'] for i in recipe['cache_images']],['System/Library/PrivateFrameworks/CoreWiFi.framework/Versions/A/CoreWiFi'])
        self.assertEqual({i['family'] for i in recipe['cache_images']},set(recipe['os_cache']['families']))
        self.assertFalse(any(i['path'].startswith(r) for i in recipe['cache_images'] for r in recipe['system_roots']))

    def test_missing_driverkit_is_explicit_failure(self):
        source=self.root/'cache';source.mkdir()
        with self.assertRaisesRegex(ValueError,'no x86_64 family'):
            tool.package(self.args('driverkit',source=source))

    def test_driverkit_only_selects_intel_family(self):
        source=self.root/'cache';source.mkdir()
        for name in ['dyld_shared_cache_x86_64','dyld_shared_cache_x86_64.01','dyld_shared_cache_arm64e']:(source/name).write_bytes(name.encode())
        manifest=tool.package(self.args('driverkit',source=source))
        self.assertEqual(manifest['totals']['driverkit-cache']['files'],2)
        self.assertFalse((self.root/'out/payload/driverkit-cache/dyld_shared_cache_arm64e').exists())

    def boot(self,digest=None):
        unpack=self.root/'unpack';p=unpack/'zip/AssetData/boot';p.mkdir(parents=True)
        (p/'BuildManifest.plist').write_bytes(plistlib.dumps({'BuildIdentities':[{'Info':{'Variant':'Customer Erase Install (IPSW)'}},{'Info':{'Variant':'Customer Software Update'}}]}))
        source=unpack/'boot.efi';source.write_bytes(b'Intel boot')
        mapping=self.root/'map.tsv'
        mapping.write_text('\n'.join('\t'.join(['preboot','GROUP/'+p,str(source.stat().st_size),digest or tool.sha(source),'matched','boot.efi']) for p in ['boot/System/Library/CoreServices/boot.efi','cryptex1/current/os.dmg','boot/System/Library/KernelCollections/BootKernelExtensions.kc'])+'\n')
        return self.args('boot',unpack=unpack,map=mapping)

    def test_boot_verifies_source_and_excludes_original_images_and_collections(self):
        manifest=tool.package(self.boot());paths={r['path'] for r in manifest['entries']}
        self.assertEqual(paths,{'boot/System/Library/CoreServices/boot.efi','cryptex1/current/BuildManifest.plist','restore/BuildManifest.plist'})
        bm=plistlib.loads((self.root/'out/payload/preboot/cryptex1/current/BuildManifest.plist').read_bytes())
        self.assertEqual(len(bm['BuildIdentities']),1)

    def test_changed_mapped_donor_fails(self):
        with self.assertRaisesRegex(ValueError,'mapped donor source differs'):
            tool.package(self.boot('0'*64))

    def test_recipe_excludes_kernel_kcs_and_derived_components(self):
        recipe=json.loads((UNIT/'recipes/25G332.json').read_text())
        for root in ['System/Library/Extensions','System/Library/Kernels','System/Library/KernelCollections','System/Library/CoreServices/OpenCore-Legacy-Patcher.plist','usr/lib/libmetalold-selectors.dylib']:
            self.assertNotIn(root,recipe['system_roots'])
        self.assertEqual(recipe['build_tools'],['usr/bin/kmutil'])

    def test_extensions_is_a_separate_recorded_role(self):
        d=json.loads(self.recipe.read_text());d['extensions_roots']=['System/Library/Extensions'];self.recipe.write_text(json.dumps(d))
        p=self.tree/'System/Library/Extensions/Fixture.kext';p.mkdir(parents=True);(p/'Info.plist').write_bytes(b'kext resource')
        with self.meta.open('a') as stream:
            for name,typ in [('System/Library/Extensions','D'),('System/Library/Extensions/Fixture.kext','D'),('System/Library/Extensions/Fixture.kext/Info.plist','F')]:
                stream.write(name+'\t'+typ+'\t0\t0\t755\t0\t12\t34\t-\n')
        manifest=tool.package(self.args('extensions'))
        self.assertEqual(set(manifest['totals']),{'extensions'})
        self.assertTrue((self.root/'out/payload/extensions/System/Library/Extensions/Fixture.kext/Info.plist').is_file())

    def test_guest_role_application_restores_manifest_ownership_without_linker(self):
        artifact=self.root/'artifact';source=artifact/'payload/system/System/Donor';source.mkdir(parents=True);(source/'file').write_bytes(b'new')
        row=dict(role='system',path='System/Donor/file',kind='file',sha256=tool.sha(source/'file'),uid=0,gid=80,mode='755',flags='20',mtime_ns=12000000034,xattrs={'com.example.test':'6162'})
        (artifact/'manifest.json').write_text(json.dumps({'entries':[row,dict(row,role='build-tools',path='usr/bin/kmutil')]}))
        target=self.root/'target';old=target/'System/Donor';old.mkdir(parents=True);(old/'file').write_bytes(b'old')
        def run(args,**kwargs):
            if args[0]=='ditto':shutil.copy2(args[-2],args[-1])
        with mock.patch.object(tool.subprocess,'run',side_effect=run),mock.patch.object(tool.os,'lchown') as owner,mock.patch.object(tool.os,'chflags') as flags,mock.patch.object(tool,'set_xattr') as attrs:
            tool.install_native(artifact,target)
        self.assertEqual((target/'System/Donor/file').read_bytes(),b'new')
        owner.assert_called_once_with(target/'System/Donor/file',0,80)
        attrs.assert_called_once_with(target/'System/Donor/file','com.example.test',b'ab')
        self.assertFalse((target/'usr/bin/kmutil').exists())

    def test_host_copy_does_not_apply_restricted_source_flags(self):
        source=self.root/'restricted';source.write_bytes(b'restricted cache source')
        target=self.root/'payload/cache'
        # copy2/copystat attempts to apply source flags and fails on mounted
        # restricted caches. Byte extraction must never call either operation.
        with mock.patch.object(tool.shutil,'copy2',side_effect=PermissionError('chflags EPERM')),mock.patch.object(tool.os,'chflags',side_effect=PermissionError('restricted')):
            tool.copy_entry(source,target)
        self.assertEqual(target.read_bytes(),source.read_bytes())
        self.assertTrue(target.stat().st_mode & 0o200)

    def test_guest_driverkit_replaces_only_supplied_intel_family(self):
        artifact=self.root/'artifact';source=artifact/'payload/driverkit-cache';source.mkdir(parents=True);(source/'dyld_shared_cache_x86_64h').write_bytes(b'intel cache')
        row=dict(role='driverkit-cache',path='dyld_shared_cache_x86_64h',kind='file',sha256=tool.sha(source/'dyld_shared_cache_x86_64h'),uid=0,gid=0,mode='644',flags='80020',mtime_ns=12000000034,xattrs={})
        (artifact/'manifest.json').write_text(json.dumps({'entries':[row]}))
        target=self.root/'combined/System/DriverKit/System/Library/dyld';target.mkdir(parents=True);(target/'dyld_shared_cache_arm64e').write_bytes(b'Golden cache')
        def run(args,**kwargs):
            if args[0]=='ditto':shutil.copy2(args[-2],args[-1])
        with mock.patch.object(tool.subprocess,'run',side_effect=run),mock.patch.object(tool.os,'lchown') as owner,mock.patch.object(tool.os,'chflags') as flags:
            tool.install_native(artifact,target,'driverkit-cache')
        self.assertEqual((target/'dyld_shared_cache_x86_64h').read_bytes(),b'intel cache')
        self.assertEqual((target/'dyld_shared_cache_arm64e').read_bytes(),b'Golden cache')
        owner.assert_called_once_with(target/'dyld_shared_cache_x86_64h',0,0)
        flags.assert_called_with(target/'dyld_shared_cache_x86_64h',0x80000,follow_symlinks=False)

    def test_hardlink_alias_inherits_authoritative_master_metadata(self):
        with self.meta.open('a') as stream:stream.write('System/Donor/alias\tH\t0\t0\t0\t0\t-\t-\t-\n')
        self.links.write_text('=System/Donor/file\n+System/Donor/alias\n')
        rows=tool.metadata(self.meta,self.links)
        self.assertEqual(rows['System/Donor/alias']['uid'],0)
        self.assertEqual(rows['System/Donor/alias']['gid'],80)
        self.assertEqual(rows['System/Donor/alias']['mode'],'755')
        self.assertEqual(rows['System/Donor/alias']['hardlink_metadata_from'],'System/Donor/file')

    def test_missing_installer_metadata_never_falls_back_to_host_owner(self):
        p=self.tree/'System/Donor';p.mkdir(parents=True);(p/'unrecorded').write_bytes(b'no metadata')
        with self.assertRaisesRegex(ValueError,'required installer metadata is absent'):
            tool.package(self.args('native'))

    def test_bootstrap_metadata_is_applied_under_the_new_group(self):
        artifact=self.root/'artifact';source=artifact/'payload/preboot/boot';source.mkdir(parents=True);(source/'boot.efi').write_bytes(b'Intel boot')
        row=dict(role='preboot',path='boot/boot.efi',kind='file',sha256=tool.sha(source/'boot.efi'),uid=0,gid=0,mode='644',flags='80020',mtime_ns=12000000034,xattrs={})
        (artifact/'manifest.json').write_text(json.dumps({'entries':[row]}))
        target=self.root/'Preboot/NEW-GROUP'
        def run(args,**kwargs):
            if args[0]=='ditto':shutil.copy2(args[-2],args[-1])
        with mock.patch.object(tool.subprocess,'run',side_effect=run),mock.patch.object(tool.os,'lchown') as owner,mock.patch.object(tool.os,'chflags') as flags:
            tool.install_native(artifact,target,'preboot')
        self.assertEqual((target/'boot/boot.efi').read_bytes(),b'Intel boot')
        owner.assert_called_once_with(target/'boot/boot.efi',0,0)
        flags.assert_called_with(target/'boot/boot.efi',0x80000,follow_symlinks=False)

    def link_artifact(self,target):
        artifact=self.root/'artifact';source=artifact/'payload/extensions/Resources';source.mkdir(parents=True)
        (source/'direct').write_bytes(b'direct file bytes');(source/'alias').symlink_to(target)
        base=dict(role='extensions',uid=os.getuid(),gid=os.getgid(),mode='755',flags='0',mtime_ns=12000000034,xattrs={})
        rows=[dict(base,path='Resources',kind='directory'),dict(base,path='Resources/direct',kind='file',sha256=tool.sha(source/'direct')),
              dict(base,path='Resources/alias',kind='symlink',link_target=target)]
        (artifact/'manifest.json').write_text(json.dumps({'entries':rows}))
        return artifact

    def test_symlink_to_regular_file_never_reads_shared_link_metadata(self):
        artifact=self.link_artifact('direct');destination=self.root/'destination'
        with mock.patch.object(tool.subprocess,'run') as command,mock.patch.object(tool.os,'chflags'),mock.patch.object(tool,'xattrs',side_effect=OSError(errno.ELOOP,'virtiofs symlink attribute lookup')):
            tool.install_native(artifact,destination,'extensions')
        self.assertEqual((destination/'Resources/alias').readlink(),Path('direct'))
        self.assertEqual((destination/'Resources/alias').read_bytes(),b'direct file bytes')
        self.assertFalse(any(c.args[0][0]=='ditto' for c in command.call_args_list))

    def test_actual_self_loop_is_preserved_without_traversal(self):
        artifact=self.link_artifact('alias');destination=self.root/'destination'
        with mock.patch.object(tool.os,'chflags'),mock.patch.object(tool,'xattrs',side_effect=OSError(errno.ELOOP,'symlink attributes')):
            tool.install_native(artifact,destination,'extensions')
        self.assertEqual((destination/'Resources/alias').readlink(),Path('alias'))
        with self.assertRaises(OSError) as caught:(destination/'Resources/alias').stat()
        self.assertEqual(caught.exception.errno,errno.ELOOP)
        self.assertEqual((destination/'Resources/direct').read_bytes(),b'direct file bytes')

    def test_regular_file_payload_changed_to_symlink_is_refused(self):
        artifact=self.link_artifact('direct');p=artifact/'payload/extensions/Resources/direct';p.unlink();p.symlink_to('direct')
        with self.assertRaises(OSError) as caught:tool.install_native(artifact,self.root/'destination','extensions')
        self.assertEqual(caught.exception.errno,errno.ELOOP)

    def test_changed_regular_payload_is_not_silently_copied(self):
        artifact=self.link_artifact('direct');(artifact/'payload/extensions/Resources/direct').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'differs from manifest'):tool.install_native(artifact,self.root/'destination','extensions')

    def test_required_generic_family_is_not_satisfied_by_haswell_cache(self):
        d=json.loads(self.recipe.read_text());d['required_driverkit_families']=['dyld_shared_cache_x86_64'];self.recipe.write_text(json.dumps(d))
        source=self.root/'cache';source.mkdir();(source/'dyld_shared_cache_x86_64h').write_bytes(b'Haswell only')
        with self.assertRaisesRegex(ValueError,'required DriverKit family missing'):
            tool.package(self.args('driverkit',source=source))

    def test_repeatable_driverkit_sources_include_generic_and_haswell(self):
        sources=[]
        for family in ['dyld_shared_cache_x86_64','dyld_shared_cache_x86_64h']:
            source=self.root/family;source.mkdir();(source/family).write_bytes(family.encode());sources.append(source)
        d=json.loads(self.recipe.read_text());d['required_driverkit_families']=['dyld_shared_cache_x86_64'];self.recipe.write_text(json.dumps(d))
        manifest=tool.package(self.args('driverkit',source=sources))
        self.assertEqual({r['path'] for r in manifest['entries']},{'dyld_shared_cache_x86_64','dyld_shared_cache_x86_64h'})
        self.assertEqual(manifest['required_driverkit_families'],['dyld_shared_cache_x86_64'])

    def test_wrong_driverkit_source_ownership_is_not_recorded_as_authoritative(self):
        source=self.root/'cache';source.mkdir();(source/'dyld_shared_cache_x86_64').write_bytes(b'generic')
        d=json.loads(self.recipe.read_text());d['driverkit_owner_uid']=os.getuid()+1;self.recipe.write_text(json.dumps(d))
        with self.assertRaisesRegex(ValueError,'mount source read-only with owners on'):
            tool.package(self.args('driverkit',source=source))

    def test_whole_root_replacement_removes_only_selected_golden_extras(self):
        artifact=self.link_artifact('direct');destination=self.root/'destination';(destination/'Resources').mkdir(parents=True)
        (destination/'Resources/golden-only').write_bytes(b'remove');(destination/'unselected-sibling').write_bytes(b'keep')
        with mock.patch.object(tool.subprocess,'run'),mock.patch.object(tool.os,'chflags'):
            tool.install_native(artifact,destination,'extensions')
        self.assertFalse((destination/'Resources/golden-only').exists())
        self.assertEqual((destination/'unselected-sibling').read_bytes(),b'keep')
        self.assertEqual((destination/'Resources/direct').read_bytes(),b'direct file bytes')

    def test_recipe_restores_all_eight_legacy_framework_bases(self):
        recipe=json.loads((UNIT/'recipes/25G332.json').read_text())
        self.assertEqual(len(recipe['legacy_framework_base_roots']),8)
        self.assertTrue(set(recipe['legacy_framework_base_roots'])<=set(recipe['system_roots']))
        self.assertEqual(len(recipe['system_roots']),50)
        self.assertEqual(recipe['replacement_policy'],'replace-maximal-declared-roots')
        self.assertEqual(recipe['required_driverkit_families'],['dyld_shared_cache_x86_64'])

    def test_missing_requested_install_role_fails(self):
        artifact=self.root/'empty';artifact.mkdir();(artifact/'manifest.json').write_text('{"entries": []}')
        with self.assertRaisesRegex(ValueError,'no requested donor role'):
            tool.install_native(artifact,self.root/'destination','recovery')

    def test_relative_paths_cannot_escape(self):
        for path in ['/System/Donor','../outside']:
            with self.assertRaises(ValueError):tool.relative(path)


if __name__=='__main__':unittest.main()
