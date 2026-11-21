"""apply.sh into a scratch Data root: the launcher, the CPython and tools/hao land where /usr/local/bin/hao expects."""
import os
import json
import shutil
import sys
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
RECIPE = HERE.parent


class Apply(unittest.TestCase):
    def test_release_archive_stages_cache_download_consumer(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name) / 'release'
            recipe = root / 'patches/hao-command'
            recipe.mkdir(parents=True)
            shutil.copy2(RECIPE / 'make.sh', recipe / 'make.sh')
            top = RECIPE.parents[1]
            shutil.copytree(top / 'tools/hao', root / 'tools/hao', ignore=shutil.ignore_patterns('__pycache__'))
            (root / 'tools/acquire').mkdir()
            shutil.copy2(top / 'tools/acquire/acquire', root / 'tools/acquire/acquire')
            shutil.copy2(top / 'NOTICE.md', root / 'NOTICE.md')
            out = Path(name) / 'staged'
            subprocess.run(['sh', str(recipe / 'make.sh'), str(out)], check=True)
            spec = json.loads((top / 'pipeline/cache-rebuild/26B131.json').read_text())
            release = out / 'release.json'
            release.write_text(json.dumps({'cache_rebuild':spec, 'assets':[
                {'row':row,'file':row+'.gz','url':'https://example.invalid/'+row+'.gz',
                 'size':1,'sha256':'0'*64} for row in spec['rows'].values()]}))
            script = """import sys, types
from pathlib import Path
sys.path.insert(0,sys.argv[1])
import cachebuild
cachebuild.preflight=lambda *a,**k:None
args=types.SimpleNamespace(work=sys.argv[3],dry_run=True,assets_from=None)
console=types.SimpleNamespace(item=print)
raise SystemExit(cachebuild.command(args,console,sys.argv[2]))
"""
            result = subprocess.run([sys.executable,'-I','-B','-c',script,
                str(out/'hao/tools/hao/src'),str(release),str(out/'no-write')],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertIn('Dry run:',result.stdout)
            self.assertFalse((out/'no-write').exists())

    def run_apply(self, d, prepare=None):
        """make.sh then apply.sh into D/data (chown left out: no root here); PREPARE(data) runs before apply.sh."""
        p = d / 'recipe'
        (p / 'inputs').mkdir(parents=True)
        (p / 'build').mkdir()
        py = d / 'python' / 'bin'
        py.mkdir(parents=True)
        (py / 'python3.12').write_text('#!/bin/sh\n')
        (py / 'python3.12').chmod(0o755)
        with tarfile.open(p / 'inputs' / 'python_x86_64', 'w:gz') as t:
            t.add(d / 'python', arcname='python')
        subprocess.run(['sh', str(RECIPE / 'make.sh'), str(p / 'build')], check=True)
        data = d / 'data'
        (data / 'usr/local/bin').mkdir(parents=True)
        if prepare:
            prepare(data)
        env = dict(os.environ, P=str(p), DATA=str(data))
        script = (RECIPE / 'apply.sh').read_text().replace('chown ', ': chown ').replace(' -o root -g wheel', '')
        return subprocess.run(['sh', '-eu', '-c', script], env=env, capture_output=True, text=True)

    def test_lays_out_the_command(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            data = d / 'data'
            # a symlink planted at the launcher's name is replaced, not written through
            victim = d / 'victim'
            victim.write_text('keep')
            r = self.run_apply(d, prepare=lambda data: (data / 'usr/local/bin/hao').symlink_to(victim))
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(victim.read_text(), 'keep')
            self.assertFalse((data / 'usr/local/bin/hao').is_symlink())
            self.assertEqual(oct((data / 'usr/local/bin/hao').stat().st_mode & 0o777), '0o755')
            self.assertEqual([x.name for x in (data / 'usr/local/bin').iterdir()], ['hao'])
            launcher = (data / 'usr/local/bin/hao').read_text()
            self.assertIn('/usr/local/lib/hao/python/bin/python3.12 -I /usr/local/lib/hao/hao/tools/hao/hao', launcher)
            self.assertTrue((data / 'usr/local/lib/hao/python/bin/python3.12').is_file())
            self.assertTrue((data / 'usr/local/lib/hao/hao/tools/hao/hao').is_file())
            self.assertTrue((data / 'usr/local/lib/hao/hao/tools/hao/vendor/rich/LICENSE').is_file())
            self.assertFalse((data / 'usr/local/lib/hao/hao/tools/hao/tests').exists())
            # hao's log folder exists 755 before first boot, so launchd never creates it 744
            self.assertEqual(oct((data / 'private/var/log/hao').stat().st_mode & 0o777), '0o755')

    def test_an_existing_744_log_folder_is_opened(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            def prepare(data):
                (data / 'private/var/log/hao').mkdir(parents=True)
                (data / 'private/var/log/hao').chmod(0o744)
            r = self.run_apply(d, prepare=prepare)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(oct((d / 'data/private/var/log/hao').stat().st_mode & 0o777), '0o755')

    def test_a_launcher_link_to_a_folder_is_replaced(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            r = self.run_apply(d, prepare=lambda data: (d / 'elsewhere').mkdir() or
                               (data / 'usr/local/bin/hao').symlink_to(d / 'elsewhere'))
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(list((d / 'elsewhere').iterdir()), [])
            self.assertTrue((d / 'data/usr/local/bin/hao').is_file())

    def test_refuses_a_symlinked_folder(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            p = d / 'recipe'
            (p / 'inputs').mkdir(parents=True)
            data = d / 'data'
            (data / 'usr').mkdir(parents=True)
            (d / 'elsewhere').mkdir()
            (data / 'usr/local').symlink_to(d / 'elsewhere')
            env = dict(os.environ, P=str(p), DATA=str(data))
            r = subprocess.run(['sh', '-eu', '-c', (RECIPE / 'apply.sh').read_text()], env=env, capture_output=True,
                               text=True)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn('is a symlink', r.stderr)
            self.assertEqual(list((d / 'elsewhere').iterdir()), [])


if __name__ == '__main__':
    unittest.main()
