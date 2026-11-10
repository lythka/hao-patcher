"""Old-LLVM runtime core, native bit semantics, ABI and concurrency controls."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from runtime.build import build
from runtime.replay_runtime import paths

HERE=Path(__file__).resolve().parent/'runtime'

@unittest.skipUnless(os.environ.get('METALLIB_RUNTIME'), 'needs METALLIB_RUNTIME, a cache/shaders replay runtime')
class RuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory(prefix='shader-runtime-test-')
        cls.root=Path(cls.temporary.name)
        llvm,_=paths()
        cls.report=build(cls.root/'build',llvm)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def test_legacy_sync_signatures_and_preservation(self):
        llvm, env = paths()
        result = subprocess.run([__import__('sys').executable, str(HERE/'check_legacy_sync.py'),
            str(self.root/'build/transform-driver'), str(llvm), str(self.root/'legacy-sync')],
            env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
        self.assertIn('16 runtime synchronization cases', result.stdout)

    def test_private_generic_provenance_and_native_values(self):
        result=subprocess.run([__import__('sys').executable,str(HERE/'check_private_generic.py'),
            str(self.root/'build/transform-driver'),str(self.root/'private-generic')],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        report=json.loads((self.root/'private-generic/results.json').read_text())
        self.assertEqual(len(report['cases']),25)
        self.assertEqual(report['cpu_comparisons'],12480)

    def test_logical32_reconvergence_and_refusals(self):
        from runtime.check_logical32 import check
        rows=check(self.root/'build/transform-driver',self.root/'logical32')
        self.assertEqual(len(rows),29)
        llvm,env=paths();env['COMPAT_LOGICAL32']='mps-sift-32x1x1'
        p=subprocess.run([str(self.root/'build/stress-driver'),str(llvm),str(self.root/'logical32/full.ll')],
                         env=env,capture_output=True,text=True)
        self.assertEqual(p.returncode,0,p.stderr[-2000:])
        self.assertIn('400 verified clones across 4 concurrent LLVM contexts',p.stdout)

    def test_logical32_shared_exchange_execution(self):
        from runtime.logical32_helpers import helpers
        ir=self.root/'exchange.ll';binary=self.root/'exchange'
        ir.write_text(helpers(False).replace('define internal ', 'define ')+
                      'declare float @air.fast_fmax.f32(float, float) nounwind readnone\n')
        p=subprocess.run(['xcrun','clang','-O2','-Wno-override-module',str(ir),str(HERE/'logical32_cpu.c'),
                          '-pthread','-o',str(binary)],capture_output=True,text=True)
        self.assertEqual(p.returncode,0,p.stderr)
        subprocess.run(['codesign','-f','-s','-',str(binary)],check=True,capture_output=True)
        p=subprocess.run([str(binary)],capture_output=True,text=True,timeout=60)
        self.assertEqual(p.returncode,0,p.stdout+p.stderr)
        self.assertIn('16384 exchange comparisons, 0 failures',p.stdout)

    def test_separate_compiler_and_gpu_client_loading(self):
        llvm,env=paths()
        env.update(COMPAT_BLOCK_READS='per-axis-mip0',COMPAT_CLIENT_ABI='axis-st-v1')
        driver=str(self.root/'build/loader-driver')
        for mode,arg,expected in [('compiler',llvm,'without GPU client or driver'),
                                  ('client',self.root/'build/gpu-client.dylib','without compiler or LLVM')]:
            p=subprocess.run([driver,mode,str(arg)],env=env,capture_output=True,text=True)
            self.assertEqual(p.returncode,0,p.stdout+p.stderr)
            self.assertIn(expected,p.stdout)
        # Without the explicit deployment ABI, compiler queries must stay disabled.
        for abi in ('', 'unknown'):
            p=subprocess.run([driver,'compiler',str(llvm)],env={**env,'COMPAT_CLIENT_ABI':abi},capture_output=True,text=True)
            self.assertEqual(p.returncode,4,p.stdout+p.stderr)

    def test_argument_returns_and_cleanup_across_threads(self):
        result=subprocess.run([str(self.root/'build/abi-driver')],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('4002 cleanup calls',result.stdout)

    def test_bound_sampler_upload_preserves_legacy_constants(self):
        import importlib.util
        rt=Path(os.environ['METALLIB_RUNTIME']).resolve()
        path=HERE.parents[3]/'cache/shaders/lib/harness.py'
        spec=importlib.util.spec_from_file_location('sampler_harness',path)
        harness=importlib.util.module_from_spec(spec);spec.loader.exec_module(harness)
        env=harness.env(str(rt),None,str(self.root/'sampler-cache'),keys=False,
                        extra={'COMPAT_BLOCK_READS':'per-axis-mip0','COMPAT_CLIENT_ABI':'axis-st-v1'})
        args=harness.cmd(str(rt));args[0]=str(self.root/'build/sampler-upload-driver');args.append(str(self.root/'build/gpu-client.dylib'))
        result=subprocess.run(args,env=env,capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertIn('64 driver upload controls passed',result.stdout)

    def test_block_reads_and_transactional_refusals(self):
        result=subprocess.run([__import__('sys').executable,str(HERE/'check_blocks.py'),
            str(self.root/'build/transform-driver'),str(self.root/'block-core')],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr+result.stdout)
        report=json.loads((self.root/'block-core/results.json').read_text())
        self.assertEqual(len(report),22)
        self.assertTrue(all(r['original_unchanged'] and r['valid'] for r in report))

    def test_core_semantics_preservation_refusal_and_concurrency(self):
        result=subprocess.run(['python3',str(HERE/'check_core.py'),str(self.root/'build/transform-driver'),str(self.root/'core')],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr+result.stdout)
        report=json.loads((self.root/'core/results.json').read_text())
        self.assertEqual(len(report['ir_controls']),51)
        self.assertEqual(report['native_lane_comparisons'],3080192)
        self.assertTrue(all(r['original_unchanged'] and r['valid'] for r in report['ir_controls']))
        llvm,env=paths()
        result=subprocess.run([str(self.root/'build/stress-driver'),str(llvm),str(self.root/'core/cpu-3.ll')],env=env,capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr[-2000:])
        self.assertIn('400 verified clones across 4 concurrent LLVM contexts',result.stdout)

    def test_null_helpers_effects_refusals_and_native_semantics(self):
        result=subprocess.run(['python3',str(HERE/'check_null.py'),str(self.root/'build/transform-driver'),str(self.root/'null-core')],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr+result.stdout)
        report=json.loads((self.root/'null-core/results.json').read_text())
        self.assertEqual(len(report['ir']),28)
        self.assertEqual(report['cpu_comparisons'],108465)
        self.assertTrue(all(r['valid'] and r['original_unchanged'] for r in report['ir']))
        llvm,env=paths()
        result=subprocess.run([str(self.root/'build/stress-driver'),str(llvm),str(self.root/'null-core/private-callback.ll')],env=env,capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr[-2000:])
        self.assertIn('400 verified clones across 4 concurrent LLVM contexts',result.stdout)

if __name__=='__main__':
    unittest.main()
