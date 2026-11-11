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

TOP=Path(__file__).resolve().parents[2]
RUN=runpy.run_path(str(TOP/'pipeline/golden-run'))


class GoldenRun(unittest.TestCase):
    def test_configuration_preserves_selected_hook_order_and_os_roles(self):
        with tempfile.TemporaryDirectory() as folder:
            share=Path(folder);(share/'kdk/KDK_25.kdk').mkdir(parents=True)
            rows=[('oclp-hook','darwin27-kernel'),('oclp-hook','golden-gate-userspace'),
                  ('oclp-hook','later-fix'),('post','post-one'),('oclp-hook','selectors')]
            (share/'patches.txt').write_text(''.join(stage+' '+name+' input\n' for stage,name in rows))
            for i,(stage,name) in enumerate(rows,1):
                path=share/('hooks' if stage=='oclp-hook' else 'post')/('%02d-%s'%(i,name));path.mkdir(parents=True)
                (path/('apply.sh' if stage=='post' else 'kc-hook.sh')).touch()
            (share/'hooks/05-selectors/hook.sh').touch()
            config=RUN['guest_config'](dict(size_bytes=80,name='GG',golden_build='26A428',donor_build='25G332'),share,'/guest/job')
            self.assertEqual(['later-fix','selectors'],[h['name'] for h in config['hooks']])
            self.assertEqual(['post-one'],[p['name'] for p in config['posts']])
            self.assertTrue(config['kernel_hook'].endswith('01-darwin27-kernel/kc-hook.sh'))
            self.assertTrue(config['userspace']['install'].endswith('02-golden-gate-userspace/install.py'))
            self.assertIn('/assembly-inputs/native',config['donors']['native'])
            self.assertEqual('/guest/job/tmp',config['environment']['TMPDIR'])
            self.assertEqual('golden-pristine.sh',Path(config['pristine_script']).name)

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
