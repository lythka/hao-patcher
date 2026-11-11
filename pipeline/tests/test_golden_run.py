"""Golden host runner/config/completion fixtures; never start a VM or invoke APFS tools."""
import contextlib
import io
import json
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

TOP=Path(__file__).resolve().parents[2]
RUN=runpy.run_path(str(TOP/'pipeline/golden-run'))


class GoldenRun(unittest.TestCase):
    def test_capture_reads_stopped_workspace_readonly_and_detaches_on_failure(self):
        state=dict(vm='/fixture/vm',guest_local='/Volumes/work/job',
                   guest_workspace=dict(mount='/Volumes/work',image='/fixture/workspace.img'),
                   recipe=dict(size_bytes=4))
        calls=[]
        def command(args,**kwargs):
            calls.append(list(map(str,args)))
            if args[:2]==['hdiutil','attach']:
                root=Path(args[args.index('-mountpoint')+1]);(root/'job').mkdir()
                (root/'job/target.img').write_bytes(b'test')
        with mock.patch('runpy.run_path',return_value={'running':lambda vm:[]}):
            with self.assertRaisesRegex(RuntimeError,'fixture capture failure'):
                with RUN['readonly_workspace'](state,command) as image:
                    self.assertEqual(image.read_bytes(),b'test')
                    raise RuntimeError('fixture capture failure')
        self.assertIn('-readonly',calls[0]);self.assertIn('-nobrowse',calls[0])
        self.assertEqual(calls[-1][:2],['hdiutil','detach'])
        calls.clear()
        with mock.patch('runpy.run_path',return_value={'running':lambda vm:[123]}):
            with self.assertRaisesRegex(RuntimeError,'must be stopped'):
                with RUN['readonly_workspace'](state,command):pass
        self.assertEqual(calls,[])

    def share(self, folder, rows, hook_sh=()):
        share=Path(folder);(share/'kdk/KDK_25.kdk').mkdir(parents=True)
        (share/'patches.txt').write_text(''.join(stage+' '+name+' input\n' for stage,name in rows))
        for i,(stage,name) in enumerate(rows,1):
            path=share/('hooks' if stage=='oclp-hook' else 'post')/('%02d-%s'%(i,name));path.mkdir(parents=True)
            (path/('apply.sh' if stage=='post' else 'hook.sh' if name in hook_sh else 'kc-hook.sh')).touch()
        return share

    def test_boot_efi_archive_carries_opencore_and_oclp_licenses(self):
        import zipfile
        with tempfile.TemporaryDirectory() as folder:
            prepared=Path(folder)/'boot-efi';(prepared/'payload/EFI/OC').mkdir(parents=True)
            (prepared/'payload/EFI/OC/OpenCore.efi').write_bytes(b'oc');(prepared/'manifest.json').write_text('{}')
            archive=RUN['boot_efi_archive'](prepared,Path(folder))
            with zipfile.ZipFile(archive) as bundle:
                names=set(bundle.namelist())
                self.assertIn('payload/EFI/OC/OpenCore.efi',names);self.assertIn('manifest.json',names)
                self.assertIn('Redistribution and use',bundle.read('LICENSES/OpenCorePkg-LICENSE.txt').decode())
                self.assertIn('Dortania',bundle.read('LICENSES/OpenCore-Legacy-Patcher-LICENSE.txt').decode())

    def test_configuration_runs_every_recipe_in_set_order(self):
        rows=[('oclp-hook','darwin27-kernel'),('oclp-hook','golden-gate-userspace'),
              ('oclp-hook','later-fix'),('oclp-hook','selectors'),('post','post-one'),('post','post-two')]
        with tempfile.TemporaryDirectory() as folder:
            share=self.share(folder,rows)
            config=RUN['guest_config'](dict(size_bytes=80,name='GG',golden_build='26A428',donor_build='25G332'),share,'/guest/job')
            self.assertEqual(['darwin27-kernel','golden-gate-userspace','later-fix','selectors'],[h['name'] for h in config['hooks']])
            self.assertTrue(all(h['kc_hook'].endswith('%02d-%s/kc-hook.sh'%(i,h['name'])) for i,h in enumerate(config['hooks'],1)))
            self.assertEqual(['post-one','post-two'],[p['name'] for p in config['posts']])
            self.assertNotIn('kernel_hook',config);self.assertNotIn('userspace',config)
            self.assertTrue(config['kernel'].endswith('01-darwin27-kernel/inputs/darwin27_kernel'))
            self.assertTrue(config['payload'].endswith('02-golden-gate-userspace/inputs/golden_gate_userspace'))
            self.assertIn('/assembly-inputs/native',config['donors']['native'])
            self.assertEqual('/guest/job/tmp',config['environment']['TMPDIR'])
            self.assertEqual('golden-pristine.sh',Path(config['pristine_script']).name)

    def test_skipped_optional_groups_merge_assembly_and_flag(self):
        self.assertEqual(RUN['skipped_groups']({}),[])
        self.assertEqual(RUN['skipped_groups']({'skip_optional':['caches']},['caches','other']),['caches','other'])
        for bad in ('caches',[''],[1]):
            with self.assertRaises(ValueError):RUN['skipped_groups']({'skip_optional':bad})

    def test_dev_build_follows_dev_status_recipes(self):
        base=[('oclp-hook','darwin27-kernel'),('oclp-hook','golden-gate-userspace'),('post','hao-opencore')]
        for rows,value in ((base,'0'),(base+[('post','dev-access')],'1')):
            with self.subTest(dev=value),tempfile.TemporaryDirectory() as folder:
                config=RUN['guest_config'](dict(size_bytes=80,name='GG'),self.share(folder,rows),'/guest/job')
                self.assertEqual(value,config['environment']['IMAGE_DEV_BUILD'])
        sets=TOP/'pipeline/sets'
        names=lambda n:[l.split('#',1)[0].strip() for l in (sets/(n+'.txt')).read_text().splitlines() if l.split('#',1)[0].strip()]
        self.assertTrue(RUN['dev_build'](names('dev-bs-test')))
        self.assertFalse(RUN['dev_build'](names('golden-gate-26B131')))

    def test_set_order_must_be_run_order(self):
        base=[('oclp-hook','darwin27-kernel'),('oclp-hook','golden-gate-userspace')]
        cases=[([('oclp-hook','golden-gate-userspace'),('oclp-hook','darwin27-kernel')],(),'must start with darwin27-kernel then golden-gate-userspace'),
               ([('oclp-hook','fix')]+base,(),'must start with darwin27-kernel then golden-gate-userspace'),
               (base+[('oclp-hook','selectors')],('selectors',),'selectors: src/hook.sh'),
               (base+[('post','opencore'),('oclp-hook','late-fix')],(),'post recipe precedes an oclp-hook')]
        for rows,hook_sh,message in cases:
            with self.subTest(message=message),tempfile.TemporaryDirectory() as folder:
                share=self.share(folder,rows,hook_sh)
                with self.assertRaisesRegex(ValueError,message):
                    RUN['guest_config'](dict(size_bytes=80,name='GG'),share,'/guest/job')

    def test_shipped_golden_sets_are_in_run_order(self):
        import importlib.util
        spec=importlib.util.spec_from_file_location('patches_module',TOP/'pipeline/patches.py')
        patches=importlib.util.module_from_spec(spec);spec.loader.exec_module(patches)
        for name in ('golden-gate-26B131','golden-gate','dev-bs-test','dev-golden-bootstrap'):
            rows=[(stage,patch) for stage,patch,_ in patches.plan(str(TOP/'pipeline/sets'/(name+'.txt')),
                                                                  str(TOP/'pipeline/targets/mbp92.json'),'25G342')]
            with self.subTest(set=name),tempfile.TemporaryDirectory() as folder:
                share=self.share(folder,rows)
                for stage,patch in rows:
                    if stage=='oclp-hook':
                        self.assertTrue((TOP/'patches'/patch/'src/kc-hook.sh').is_file(),patch)
                        self.assertFalse((TOP/'patches'/patch/'src/hook.sh').exists(),patch)
                RUN['set_order'](share)

    def test_release_26B131_set_is_the_dev_set_without_dev_recipes(self):
        def names(name):
            return [l.split('#',1)[0].strip() for l in (TOP/'pipeline/sets'/(name+'.txt')).read_text().splitlines()
                    if l.split('#',1)[0].strip()]
        def dev(name):
            return json.loads((TOP/'patches'/name/'patch.json').read_text()).get('status')=='dev'
        self.assertEqual([n for n in names('dev-bs-test') if not dev(n)],names('golden-gate-26B131'))
        assembly=json.loads((TOP/'pipeline/assemblies/mbp92-26B131.json').read_text())
        developer=json.loads((TOP/'pipeline/assemblies/dev-26B131.json').read_text())
        self.assertEqual('golden-gate-26B131',assembly.pop('patch_set'));developer.pop('patch_set')
        self.assertEqual(developer,assembly)

    def fixture_job(self, root, rc, workspace=None):
        share=root/'share';source=share/'assembly-source';source.mkdir(parents=True)
        for name in ('post','hooks','site','py/bin'):(share/name).mkdir(parents=True)
        python=share/'py/bin/python3.12';python.write_text('#!/bin/sh\nexec '+sys.executable+' "$@"\n');python.chmod(0o755)
        shutil.copy2(TOP/'pipeline/stages/copy-hooks.py',source/'copy-hooks.py')
        script='''import json,pathlib,sys
c=json.load(open(sys.argv[1]));w=pathlib.Path(c['work']);w.mkdir()
(w/'command-000.log').write_text('retained command evidence')
(w/'build.json').write_text(json.dumps(dict(status='assembled' if c['rc']==0 else 'failed',attached_device=None)))
print('RESULT rc=0',flush=True)
if c['rc']==0:pathlib.Path(c['image']).write_bytes(b'regular raw image fixture')
raise SystemExit(c['rc'])
'''
        (source/'golden-image.py').write_text(script)
        local=root/'guest-local'
        (share/'guest-config.json').write_text(json.dumps(dict(work=str(local/'work'),image=str(local/'target.img'),rc=rc)))
        text=RUN['job_script'](str(local),str(share),'%d:%d'%(os.getuid(),os.getgid()),workspace=workspace)
        environment=dict(os.environ);environment.pop('SSDVM_AGENT_ID',None)
        result=subprocess.run(['/bin/sh'],input=text,text=True,capture_output=True,env=environment)
        self.assertEqual(0,result.returncode,result.stderr)
        return share/'out-golden'

    def test_formatter_failure_stops_before_local_copy_or_image_creation(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            output=self.fixture_job(root,0,dict(size_bytes=160*1024**3,mount='/Volumes/not-mounted-by-fixture',name='fixture'))
            self.assertEqual('1',(output/'result').read_text().strip())
            self.assertIn('refusing host execution',(output/'golden.log').read_text())
            self.assertFalse((root/'guest-local').exists())
            self.assertFalse((output/'target.img').exists())

    def test_host_workspace_creation_is_exclusive_sparse_regular_file(self):
        with tempfile.TemporaryDirectory() as folder:
            image=Path(folder)/'workspace.img'
            RUN['create_workspace_image'](image,160*1024**3)
            self.assertEqual(160*1024**3,image.stat().st_size)
            self.assertEqual(0,image.stat().st_blocks)
            with self.assertRaises(FileExistsError):RUN['create_workspace_image'](image,1)
            self.assertEqual(160*1024**3,image.stat().st_size)

    def test_actual_shell_job_exports_only_after_success_and_retains_evidence(self):
        with tempfile.TemporaryDirectory() as folder:
            output=self.fixture_job(Path(folder),0)
            self.assertEqual('0', (output/'result').read_text().strip())
            self.assertEqual(b'regular raw image fixture',(output/'target.img').read_bytes())
            self.assertEqual('retained command evidence',(output/'guest/command-000.log').read_text())
            self.assertEqual('assembled',RUN['assembled'](output)['status'])

    def test_actual_shell_failure_retains_logs_and_does_not_export_image(self):
        with tempfile.TemporaryDirectory() as folder:
            output=self.fixture_job(Path(folder),7)
            self.assertEqual('7',(output/'result').read_text().strip())
            self.assertFalse((output/'target.img').exists())
            self.assertIn('RESULT rc=0',(output/'golden.log').read_text())
            self.assertTrue((output/'guest/build.json').is_file())
            with self.assertRaises(RuntimeError):RUN['assembled'](output)

    def test_nested_result_does_not_authorize_early_completion(self):
        with tempfile.TemporaryDirectory() as folder:
            output=Path(folder);(output/'golden.log').write_text('RESULT rc=0\n')
            def complete():
                time.sleep(0.05);(output/'result.new').write_text('9\n'); (output/'result.new').replace(output/'result')
            thread=threading.Thread(target=complete);thread.start()
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(9,RUN['wait_job'](output,2,poll=0.005))
            thread.join()

    def test_attached_or_failed_record_cannot_authorize_capture(self):
        with tempfile.TemporaryDirectory() as folder:
            output=Path(folder);(output/'guest').mkdir()
            for status,device in [('failed',None),('assembled','/dev/disk99')]:
                (output/'guest/build.json').write_text(json.dumps(dict(status=status,attached_device=device)))
                with self.assertRaises(RuntimeError):RUN['assembled'](output)

    def test_lock_is_exclusive_and_removed_after_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            lock=Path(folder)/'builder.lock'
            with self.assertRaisesRegex(RuntimeError,'fixture failure'):
                with RUN['builder_lock'](lock,'golden fixture'):
                    self.assertEqual('golden fixture\n',(lock/'owner').read_text())
                    with self.assertRaises(FileExistsError):
                        with RUN['builder_lock'](lock,'other'):pass
                    raise RuntimeError('fixture failure')
            self.assertFalse(lock.exists())


if __name__=='__main__':unittest.main()
