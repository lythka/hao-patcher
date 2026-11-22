import importlib.util
import json
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch
import uuid

TOP = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(TOP/'tools/hao/src'))
module = importlib.util.spec_from_file_location('install_cache_rebuild', Path(__file__).parents[1]/'src/rebuild.py')
R = importlib.util.module_from_spec(module); module.loader.exec_module(R)
C = R.cache


class Rebuild(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory(); self.addCleanup(t.cleanup); self.root = Path(t.name)
        self.system = self.root/'system'; self.system.mkdir()
        self.os = self.root/'os'; self.os.mkdir()
        self.work = self.root/'work'; self.work.mkdir()
        version = self.system/C.VERSION; version.parent.mkdir(parents=True)
        version.write_bytes(plistlib.dumps({'ProductBuildVersion':'TEST'}))
        self.source = self.os/'System/Library/dyld'/C.BASE; self.source.parent.mkdir(parents=True)
        self.source.write_bytes(self.header(1))
        self.inventory = dict(install_space_bytes=40_000_000_000,files=[dict(view='system',path='/'+C.VERSION,
                                  size=version.stat().st_size,sha256=C.sha256(version))])
        self.spec = dict(format=1,build='TEST',source_cache_uuid=str(uuid.UUID(int=1)),
                         cache_uuid=str(uuid.UUID(int=2)),files={C.BASE:__import__('hashlib').sha256(self.header(2)).hexdigest()},
                         rows=dict(builder='builder',catalog='catalog'),features='test',space_bytes=55_000_000_000)
        self.assets = {}
        for role in ('builder','catalog'):
            p=self.root/role;p.write_bytes(role.encode());self.assets[role]=(p,C.sha256(p))
        self.console=types.SimpleNamespace(item=lambda text:None)

    def header(self, n):
        return b'dyld_v1'.ljust(88,b'\0')+uuid.UUID(int=n).bytes

    def produce(self, *args, **kwargs):
        work=args[7];family=work/'family';family.mkdir()
        (family/C.BASE).write_bytes(self.header(2))
        for name in ('build.json','provider-provenance.json','retired-roots.txt'):
            (family/name).write_text('{}')
        for name in ('producer','build'): (work/name).mkdir()
        (work/'catalog.json').write_text('{}')
        return family

    def prepare(self, binding='target'):
        with patch.object(C.os,'geteuid',return_value=0), patch.object(C.shutil,'disk_usage',return_value=types.SimpleNamespace(free=100_000_000_000)):
            return R.prepare(self.spec,self.inventory,self.assets,(self.system,self.os,self.os,self.os),
                             self.work,binding,self.console)

    def test_valid_output_reused_and_target_binding_checked(self):
        with patch.object(C,'build',side_effect=self.produce) as build:
            family=self.prepare();self.assertTrue((family/C.BASE).exists())
            self.prepare();self.assertEqual(build.call_count,1)
            with self.assertRaisesRegex(R.Stopped,'another install'): self.prepare('other')
        self.assertFalse((self.work/'build').exists())
        (family/C.BASE).write_bytes(b'wrong')
        with self.assertRaises(R.Stopped):self.prepare()

    def test_metadata_tampering_refused_on_reuse(self):
        with patch.object(C,'build',side_effect=self.produce):family=self.prepare()
        (family/'retired-roots.txt').write_text('changed')
        with self.assertRaisesRegex(R.Stopped,'metadata changed'):self.prepare()

    def test_failed_build_restarts_scratch_and_never_installs(self):
        def interrupted(*args,**kw):
            (self.work/'build').mkdir();(self.work/'build/junk').write_text('partial')
            raise R.Stopped('interrupted')
        with patch.object(C,'build',side_effect=interrupted):
            with self.assertRaises(R.Stopped):self.prepare()
        self.assertFalse((self.work/'prepared.json').exists())
        with patch.object(C,'build',side_effect=self.produce):self.prepare()
        self.assertTrue((self.work/'prepared.json').is_file())

    def test_partial_scratch_is_reclaimed_before_space_check(self):
        (self.work/'build').mkdir(); (self.work/'build/large').write_text('incomplete')
        def usage(path):
            return types.SimpleNamespace(free=1 if (self.work/'build').exists() else 100_000_000_000)
        with patch.object(C.shutil,'disk_usage',side_effect=usage), patch.object(C,'build',side_effect=self.produce):
            R.prepare(self.spec,self.inventory,self.assets,(self.system,self.os,self.os,self.os),
                      self.work,'target',self.console,euid=0)

    def test_cleanup_after_verified_checkpoint_is_finished_on_reuse(self):
        with patch.object(C,'build',side_effect=self.produce):self.prepare()
        (self.work/'build').mkdir();(self.work/'build/large').write_text('left after checkpoint')
        with patch.object(C,'build') as build:
            self.prepare();build.assert_not_called()
        self.assertFalse((self.work/'build').exists())

    def test_installed_manifest_verifies_all_files_and_uses_grafted_paths(self):
        with patch.object(C,'build',side_effect=self.produce): family=self.prepare()
        manifest=R.installed_manifest(family,self.spec)
        import verify
        root=self.root/'booted';root.mkdir()
        for relative,meta in manifest['files'].items():
            path=root/relative;path.parent.mkdir(parents=True,exist_ok=True)
            path.write_bytes((family/Path(relative).name).read_bytes())
            path.chmod(int(meta['mode'],8))
        result=verify.verify(root,[('cache.json',manifest)])
        self.assertEqual(result['problems'],[]);self.assertEqual(result['ok'],1)
        (root/next(iter(manifest['files']))).write_bytes(b'corrupt')
        self.assertEqual(verify.verify(root,[('cache.json',manifest)])['problems'][0]['problem'],'content')

    def test_wrong_input_and_asset_stop_before_builder(self):
        with patch.object(C,'build') as build:
            self.assets['builder'][0].write_bytes(b'changed')
            with self.assertRaisesRegex(R.Stopped,'asset hash'):self.prepare()
            (self.system/C.VERSION).write_bytes(b'changed')
            with self.assertRaisesRegex(R.Stopped,'source differs'):self.prepare()
            build.assert_not_called()

    def test_inventory_cannot_escape_or_be_empty(self):
        for path in ('/../outside','relative'):
            self.inventory['files'][0]['path']=path
            with self.assertRaises(R.Stopped):R.audit_sources(self.inventory,self.system,self.os)
        with self.assertRaises(R.Stopped):R.audit_sources(dict(files=[]),self.system,self.os)

    def test_offline_arm64_allowed_but_space_and_build_still_checked(self):
        C.preflight(self.spec,self.system,self.os,self.work,machine='arm64',euid=0,offline=True,free=100_000_000_000)
        with self.assertRaises(R.Stopped):C.preflight(self.spec,self.system,self.os,self.work,machine='arm64',euid=0)
        with self.assertRaises(R.Stopped):C.preflight(self.spec,self.system,self.os,self.work,machine='arm64',euid=0,offline=True,free=1)
        self.spec['build']='OTHER'
        with self.assertRaises(R.Stopped):C.preflight(self.spec,self.system,self.os,self.work,machine='x86_64',euid=0,offline=True)

    def test_install_keeps_volumes_mounted_and_detaches_source_images(self):
        from contextlib import contextmanager
        here=self.root/'hook'; config=here/'build/cache';config.mkdir(parents=True)
        (config/'config.json').write_text(json.dumps(self.spec))
        (config/'inputs.json').write_text(json.dumps(dict(self.inventory,format=1,build='TEST')))
        for role in ('builder','catalog'):
            (config/(role+'.pin')).write_text(self.assets[role][1]+' ignored-path\n')
        data=self.root/'data';data.mkdir();preboot=self.root/'preboot';preboot.mkdir()
        workspace=data/'private/var/db/hao/cache-install/G';workspace.mkdir(parents=True)
        (workspace/'prepared.json').write_text('{}')
        active=set();events=[]
        @contextmanager
        def volume(device, folder):
            active.add(device)
            try:yield data if device=='data' else preboot
            finally:active.remove(device)
        @contextmanager
        def image(path, folder):
            name=folder.name;active.add(name);folder.mkdir()
            if name=='installed-os':
                family=folder/'System/Library/dyld';family.mkdir(parents=True)
                (family/C.BASE).write_bytes(self.header(2))
            try:yield folder
            finally:active.remove(name)
        def install(system, family):
            self.assertIn('data',active);self.assertIn('preboot',active)
            self.assertNotIn('os',active);self.assertNotIn('app',active);events.append('fold')
        modules=dict(fold=types.SimpleNamespace(install=install),cryptex=types.SimpleNamespace(image_mount=image),
                     install=types.SimpleNamespace(volume_mount=volume))
        with patch.object(R,'HERE',here),patch.dict(sys.modules,modules),patch.dict(R.os.environ,dict(PBU='preboot',DATU='data',SYSU='system',GRP='G')),patch.object(R.os,'geteuid',return_value=0),patch.object(C.safety,'others_can_change',return_value=None),patch.object(R,'prepare',return_value=workspace/'family'),patch.object(R.shutil,'disk_usage',return_value=types.SimpleNamespace(free=100_000_000_000)):
            R.main([str(self.system),'install'])
        self.assertEqual(events,['fold']);self.assertEqual(active,set())
        self.assertTrue((data/'private/var/db/hao/manifests/shared-cache-family.json').is_file())
        self.assertFalse(workspace.exists())

    def test_staged_recipe_contains_offline_builder_and_pins(self):
        out=self.root/'stage/build'
        subprocess.run(['sh',str(Path(__file__).parents[1]/'make.sh'),str(out)],check=True)
        for name in ('cachebuild.py','console.py','safety.py','config.json','inputs.json',
                     'shared_cache_builder.pin','swift_metadata_catalog.pin'):
            self.assertTrue((out/'cache'/name).is_file())
        self.assertTrue((out/'fold/fold.py').is_file())
        script=out.parent/'rebuild.py'
        script.write_bytes((Path(__file__).parents[1]/'src/rebuild.py').read_bytes())
        command='import runpy; value=runpy.run_path('+repr(str(script))+'); print(value["cache"].__file__)'
        result=subprocess.run([sys.executable,'-I','-c',command],capture_output=True,text=True,check=True)
        self.assertEqual(Path(result.stdout.strip()),(out/'cache/cachebuild.py').resolve())
