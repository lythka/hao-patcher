"""The builder snapshot is deterministic and independent of working-tree edits."""
import hashlib
from pathlib import Path
import runpy
import subprocess
import tarfile
import tempfile
import unittest

KIT = runpy.run_path(str(Path(__file__).resolve().parents[1] / 'builder-kit.py'))


class BuilderKitTests(unittest.TestCase):
    def test_frozen_commit_and_archive_metadata(self):
        with tempfile.TemporaryDirectory() as folder:
            top = Path(folder)
            def git(*args):
                return subprocess.check_output(['git', '-C', str(top), *args], stderr=subprocess.DEVNULL).decode().strip()
            git('init')
            (top / 'pipeline').mkdir()
            (top / 'pipeline/tool').write_text('frozen\n')
            (top / 'secret').write_text('not a builder dependency')
            git('add', '.')
            git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.org', 'commit', '-m', 'fixture')
            commit = git('rev-parse', 'HEAD')
            first, second = top / 'one.tar.gz', top / 'two.tar.gz'
            KIT['build'](top, commit, first)
            (top / 'pipeline/tool').write_text('changed after release\n')
            git('add', 'pipeline/tool')
            git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.org', 'commit', '-m', 'development')
            KIT['build'](top, commit, second)
            self.assertEqual(first.read_bytes(), second.read_bytes())
            with tarfile.open(first) as archive:
                self.assertNotIn('secret', archive.getnames())
                self.assertEqual(archive.extractfile('pipeline/tool').read(), b'frozen\n')
                self.assertEqual(archive.extractfile('pipeline/SOURCE_COMMIT').read().decode().strip(), commit)
                for member in archive:
                    self.assertEqual((member.uid, member.gid, member.mtime), (0, 0, 0))

    def test_cut_pins_infrastructure_and_survives_development(self):
        import copy
        import json
        with tempfile.TemporaryDirectory() as folder:
            top = Path(folder)/'repo'
            top.mkdir()
            def git(*args):
                return subprocess.check_output(['git','-C',str(top),*args],stderr=subprocess.DEVNULL).decode().strip()
            git('init')
            original = Path(__file__).resolve().parents[2]
            for name in ('pipeline/bootstrap.sh','tools/hao/data/release-keys.pub'):
                p = top/name
                p.parent.mkdir(parents=True,exist_ok=True)
                p.write_bytes((original/name).read_bytes())
            downloads = []
            for arch in ('arm64','x86_64'):
                p = top/'store'/('python-'+arch+'.tar.gz')
                p.parent.mkdir(exist_ok=True)
                p.write_bytes(arch.encode())
                digest = hashlib.sha256(p.read_bytes()).hexdigest()
                row = top/'pipeline/inputs/common'/('python_'+arch)
                row.parent.mkdir(parents=True,exist_ok=True)
                row.write_text(digest+' '+str(p.relative_to(top))+'\n')
                downloads.append(dict(row=row.name,name=p.name,url='https://example.org/'+p.name,
                                      size=p.stat().st_size,sha256=digest))
            git('add','.')
            git('-c','user.name=Fixture','-c','user.email=fixture@example.org','commit','-m','release')
            commit = git('rev-parse','HEAD')
            release = dict(release='27.2.1-26B131.0',version='v0.0.1',candidate=True,
                           golden_build='26B131',donor_build='25G342',hao_patcher=commit,
                           downloads=downloads,assets=[])
            cuts = []
            for n in (1,2):
                stage = Path(folder)/str(n)
                stage.mkdir()
                rel = copy.deepcopy(release)
                KIT['release_assets'](top,rel,stage)
                for entry in rel['assets']:
                    self.assertEqual(hashlib.sha256((stage/entry['file']).read_bytes()).hexdigest(),entry['sha256'])
                    self.assertEqual(entry['commit'],commit)
                self.assertEqual({x['role'] for x in rel['assets']},{'bootstrap','builder-kit'})
                self.assertTrue(all('/hao-assets/releases/download/' in x['url'] for x in rel['downloads']))
                cuts.append({p.name:p.read_bytes() for p in stage.iterdir()})
                if n == 1:
                    (top/'pipeline/bootstrap.sh').write_text('new development bootstrap\n')
                    git('add','pipeline/bootstrap.sh')
                    git('-c','user.name=Fixture','-c','user.email=fixture@example.org','commit','-m','development')
            self.assertEqual(cuts[0],cuts[1])
