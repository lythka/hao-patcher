"""Portable deployment and cache isolation; no GUI, service or GPU submission."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from runtime.deployment import bundle, environment, MPS_LIBRARY
from runtime.build import sha


class EnvironmentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='metal-environment-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.package = self.root / 'package'
        for name in ('compiler/fw/compiler', 'compiler/dl/runtime', 'compiler/paths.dylib',
                     'libraries/default.metallib', 'translator/compat.dylib',
                     'translator/gpu-client.dylib', 'translator/sampler-query.dylib',
                     'translator/libLegacyLLVM.dylib'):
            p = self.package / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(name.encode())
        (self.package / 'build.json').write_text(json.dumps(
            dict(kind='private-translation-bundle', cache_identity='stale-report')))

    def env(self, role='compiler', **kw):
        return environment(self.package, role, self.root / 'cache', **kw)

    def test_roles_and_options_do_not_leak_from_parent(self):
        parent = dict(PATH='/usr/bin', DYLD_INSERT_LIBRARIES='/old/compat.dylib',
                      COMPAT_LOGICAL32='mps-sift-32x1x1', COMPAT_DISABLE='1',
                      MTL_MONOLITHIC_COMPILER='1', RP_ROOT='/old/root')
        client = self.env('gpu-client', inherited=parent)
        compiler = self.env('compiler', inherited=parent)
        self.assertNotIn('COMPAT_LOGICAL32', client)
        self.assertNotIn('COMPAT_DISABLE', client)
        self.assertNotIn('MTL_MONOLITHIC_COMPILER', client)
        self.assertEqual(client['PATH'], parent['PATH'])
        self.assertTrue(client['DYLD_INSERT_LIBRARIES'].endswith('/gpu-client.dylib'))
        self.assertNotIn('/compat.dylib', client['DYLD_INSERT_LIBRARIES'])
        self.assertNotIn('/gpu-client.dylib', compiler['DYLD_INSERT_LIBRARIES'])
        self.assertEqual(client['RP_CACHE_DIR'], compiler['RP_CACHE_DIR'])
        logical = self.env('compiler', reviewed_mps_dispatch=True)
        self.assertNotEqual(logical['RP_CACHE_DIR'], compiler['RP_CACHE_DIR'])
        self.assertEqual(logical['COMPAT_LOGICAL32'], 'mps-sift-32x1x1')

    def test_cache_changes_with_real_repair_bytes_even_with_stale_report(self):
        previous = self.env()['RP_CACHE_DIR']
        for name in ('libraries/default.metallib', 'compiler/dl/runtime',
                     'translator/gpu-client.dylib', 'translator/compat.dylib',
                     'translator/sampler-query.dylib', 'translator/libLegacyLLVM.dylib'):
            p = self.package / name
            p.write_bytes(p.read_bytes() + b' changed')
            current = self.env()['RP_CACHE_DIR']
            self.assertNotEqual(previous, current, name)
            previous = current

    def test_relocation_keeps_cache_identity_but_updates_load_paths(self):
        original = self.package
        first = self.env()
        self.package = self.root / 'moved package'
        original.rename(self.package)
        second = self.env()
        self.assertEqual(first['RP_CACHE_DIR'], second['RP_CACHE_DIR'])
        self.assertNotIn(str(original), json.dumps(second))
        self.assertIn(str(self.package), second['DYLD_FRAMEWORK_PATH'])
        with self.assertRaises(ValueError):
            self.env('unknown')


@unittest.skipUnless(all(os.environ.get(k) for k in
                       ('METALLIB_RUNTIME', 'METALLIB_DEPLOYMENT_PAYLOAD',
                        'METALLIB_DEPLOYMENT_LIBRARIES')), 'needs stored deployment inputs')
class NativeDeploymentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='metal-deployment-')
        cls.addClassCleanup(cls.temp.cleanup)
        cls.root = Path(cls.temp.name)
        cls.original = cls.root / 'original'
        cls.report = bundle(cls.original, os.environ['METALLIB_RUNTIME'],
                            os.environ['METALLIB_DEPLOYMENT_PAYLOAD'],
                            os.environ['METALLIB_DEPLOYMENT_LIBRARIES'])
        cls.package = cls.root / 'relocated bundle'
        cls.original.rename(cls.package)

    def test_relocated_compiler_and_client_load_in_distinct_processes(self):
        p = self.package
        llvm = p / 'translator/libLegacyLLVM.dylib'
        self.assertFalse(llvm.is_symlink())
        for role, mode, arg, expected in (
            ('compiler', 'compiler', llvm, 'without GPU client or driver'),
            ('gpu-client', 'client', p / 'translator/gpu-client.dylib', 'without compiler or LLVM')):
            env = environment(p, role, self.root / 'cache')
            result = subprocess.run([str(p / 'translator/loader-driver'), mode, str(arg)],
                                    env=env, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn(expected, result.stdout)
            self.assertNotIn(str(self.original), result.stdout + result.stderr)

    def test_existing_shader_and_helper_repairs_are_preserved(self):
        for variable, target in (('METALLIB_DEPLOYMENT_LIBRARIES', 'libraries'),
                                 ('METALLIB_DEPLOYMENT_PAYLOAD', 'compiler/root')):
            source = Path(os.environ[variable])
            for original in (source / 'System').rglob('*'):
                if original.is_file():
                    copy = self.package / target / original.relative_to(source)
                    self.assertEqual(sha(original), sha(copy), str(original))
        self.assertTrue((self.package / 'libraries' / MPS_LIBRARY).is_file())
        for path in self.package.rglob('*'):
            if path.is_symlink():
                self.assertTrue(path.resolve().is_relative_to(self.package), str(path))
        self.assertFalse(self.report['system_service_integration'])
        self.assertFalse(self.report['automatic_dispatch_enforcement'])


if __name__ == '__main__':
    unittest.main()
