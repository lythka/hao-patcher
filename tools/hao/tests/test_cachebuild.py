import gzip
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import plistlib
import sys
import tarfile
import tempfile
import types
import unittest
from unittest.mock import patch
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import cachebuild as C
from console import Stopped


class CacheBuild(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.system = self.root / 'system'
        self.os = self.root / 'os'
        self.spec = dict(format=1, build='TEST', source_cache_uuid=str(uuid.UUID(int=1)),
                         cache_uuid=str(uuid.UUID(int=2)), features='test', space_bytes=55_000_000_000,
                         rows={'builder':'builder', 'catalog':'catalog'}, files={C.BASE:'0'*64})
        version = self.system / C.VERSION
        version.parent.mkdir(parents=True)
        version.write_bytes(plistlib.dumps({'ProductBuildVersion':'TEST'}))
        self.cache(self.os / 'System/Library/dyld' / C.BASE, 1)

    def cache(self, path, identity):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'dyld_v1'.ljust(88,b'\0') + uuid.UUID(int=identity).bytes)
        return path

    def preflight(self, **kw):
        C.preflight(self.spec, self.system, self.os, self.root, **dict(
            {'machine':'x86_64','euid':0,'free':100_000_000_000}, **kw))

    def test_refusals(self):
        for kw, code in [({'machine':'arm64'},5), ({'euid':501},5), ({'free':1},3)]:
            with self.subTest(kw=kw), self.assertRaises(Stopped) as cm:
                self.preflight(**kw)
            self.assertEqual(cm.exception.code, code)
        (self.system/C.VERSION).write_bytes(plistlib.dumps({'ProductBuildVersion':'OTHER'}))
        with self.assertRaises(Stopped) as cm:
            self.preflight()
        self.assertEqual(cm.exception.code,5)

    def test_missing_source_cache_is_a_refusal(self):
        (self.os/'System/Library/dyld'/C.BASE).unlink()
        with self.assertRaises(Stopped) as cm:
            self.preflight()
        self.assertEqual(cm.exception.code,4)

    def test_source_cache_mismatch(self):
        self.cache(self.os/'System/Library/dyld'/C.BASE, 9)
        with self.assertRaises(Stopped) as cm:
            self.preflight()
        self.assertEqual(cm.exception.code,4)

    def test_exact_output_set_uuid_and_hash(self):
        family = self.root/'family'
        path = self.cache(family/C.BASE, 2)
        self.spec['files'][C.BASE] = C.sha256(path)
        self.assertEqual(C.verify_family(family,self.spec),self.spec['files'])
        sidecar = family/(C.BASE+'.unexpected'); sidecar.write_text('extra')
        with self.assertRaises(Stopped): C.verify_family(family,self.spec)
        sidecar.unlink()
        path.write_bytes(path.read_bytes()+b'changed')
        with self.assertRaises(Stopped): C.verify_family(family,self.spec)
        self.cache(path, 3)
        self.spec['files'][C.BASE] = C.sha256(path)
        with self.assertRaises(Stopped): C.verify_family(family,self.spec)

    def test_archive_rejects_traversal_and_links(self):
        for index,name in enumerate(('../escape','/absolute','link')):
            archive=self.root/('bad%d.tar.gz'%index)
            with tarfile.open(archive,'w:gz') as tar:
                info=tarfile.TarInfo(name)
                if name=='link': info.type=tarfile.SYMTYPE; info.linkname='/etc'
                tar.addfile(info,io.BytesIO())
            with self.assertRaises(Stopped): C.extract(archive,self.root/('out%d'%index))
        self.assertFalse((self.root/'escape').exists())

    def test_config_rejects_unpinned_or_unsafe_output(self):
        C.configuration({'cache_rebuild':self.spec})
        for name in ('../outside',C.BASE+'/file',C.BASE+'.'):
            self.spec['files']={C.BASE:'0'*64,name:'0'*64}
            with self.assertRaises(Stopped): C.configuration({'cache_rebuild':self.spec})

    def test_dry_run_does_not_fetch_or_create_workspace(self):
        release=self.root/'release.json'
        release.write_text(json.dumps({'cache_rebuild':self.spec}))
        work=self.root/'never-created'
        args=types.SimpleNamespace(work=work,dry_run=True,assets_from=None)
        console=types.SimpleNamespace(item=lambda *a,**k:None)
        fake={'load':lambda *a,**k:[{'row':n,'size':100} for n in ('builder','catalog')],
              'fetch':lambda *a,**k:self.fail('dry run fetched inputs')}
        with patch.object(C,'preflight'),patch.object(C.runpy,'run_path',return_value=fake):
            self.assertEqual(C.command(args,console,release),0)
        self.assertFalse(work.exists())

    def test_failed_producer_cannot_write_verified_result(self):
        workspace=self.root/'work'; workspace.mkdir()
        catalog=self.root/'catalog.gz'
        with gzip.open(catalog,'wt') as f: json.dump({'source_cache_uuid':self.spec['source_cache_uuid']},f)
        console=types.SimpleNamespace(item=lambda *a,**k:None)
        with patch.object(C,'extract'):
            with self.assertRaises(Stopped):
                C.build(self.spec,self.root/'builder',catalog,self.system,self.os,self.os,self.os,
                        workspace,console,run=lambda *a,**k:types.SimpleNamespace(returncode=1))
        self.assertFalse((workspace/'result.json').exists())


if __name__=='__main__': unittest.main()
