"""Real legacy reflection and persistent cache identities, on the host.

Set METAL_FRAMEBUFFER_RUNTIME to a cache/shaders prepared runtime. The modeled
device executes the actual MetalOld/3802 compiler, not a stand-in compiler.
"""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

UNIT = Path(__file__).resolve().parents[1]
ROOT = UNIT.parents[2]
sys.path.insert(0, str(ROOT/'cache/shaders/lib'))
import harness


@unittest.skipUnless(os.environ.get('METAL_FRAMEBUFFER_RUNTIME'), 'Requires METAL_FRAMEBUFFER_RUNTIME')
class Frontend(unittest.TestCase):
    def test_reflection_and_cold_warm_identity(self):
        runtime = Path(os.environ['METAL_FRAMEBUFFER_RUNTIME']).resolve()
        with tempfile.TemporaryDirectory(prefix='metal-framebuffer-frontend-') as directory:
            work = Path(directory)
            bundle = work/'bundle'
            subprocess.run([sys.executable, '-I', str(UNIT/'metal-framebuffer'), 'build', str(bundle)],
                           check=True, capture_output=True, text=True)
            obj, executable = work/'replay.o', work/'replay'
            subprocess.run(['xcrun', 'clang', '-arch', 'x86_64', '-mmacosx-version-min=13.0',
                '-I', str(ROOT/'cache/shaders/src'), '-I', str(UNIT/'src'), '-c',
                str(UNIT/'tests/frontend-replay.m'), '-o', str(obj)], check=True, capture_output=True)
            subprocess.run(['xcrun', 'clang++', '-std=c++17', '-arch', 'x86_64', '-mmacosx-version-min=13.0',
                '-fobjc-arc', '-framework', 'Foundation', str(obj), str(UNIT/'src/frontend.mm'),
                str(UNIT/'src/lower.cpp'), '-o', str(executable)], check=True, capture_output=True)
            subprocess.run(['codesign', '-s', '-', str(executable)], check=True, capture_output=True)
            library = bundle/'probe.metallib'
            jobs = work/'jobs.tsv'
            jobs.write_text(''.join('\t'.join([f's{s}', str(library), 'fullscreen', str(library), 'fetch0',
                f'c0.pixelFormat=115;rasterSampleCount={s};opt=1'])+'\n' for s in (1, 4)))
            identities = {}
            (work/'cache').mkdir()
            for label, live in [('original', False), ('cold', True), ('warm', True)]:
                out = work/label; out.mkdir()
                extra = {'METAL_FRAMEBUFFER_LLVM': '/System/Library/PrivateFrameworks/GPUCompiler.framework/Versions/32023/Libraries/libLLVM.dylib'}
                if live: extra['FB_LIVE'] = '1'
                env = harness.env(str(runtime), None, str(work/'cache'), str(out), extra=extra)
                run = subprocess.run([str(executable), *harness.cmd(str(runtime))[1:], str(jobs), '', 'render'],
                                     env=env, capture_output=True, text=True, timeout=90)
                self.assertEqual(run.returncode, 0, run.stderr[-4000:])
                rows = [json.loads(line.removeprefix('FB_IDENTITY ')) for line in run.stderr.splitlines()
                        if line.startswith('FB_IDENTITY ')]
                self.assertEqual(len(rows), 2, run.stderr)
                identities[label] = rows
                for row in rows:
                    self.assertTrue(row['pipeline'])
                    if live:
                        self.assertNotEqual(row['before'], row['after'])
                        self.assertIn({'name': '__fbcompat_fb_attachment_0', 'index': 127, 'type': 2}, row['bindings'])
                    else:
                        self.assertEqual(row['before'], row['after'])
                requests = int(re.search(r'done, requests=(\d+)', run.stderr)[1])
                self.assertEqual(requests, 0) if label == 'warm' else self.assertGreater(requests, 0)
            self.assertEqual(identities['cold'], identities['warm'])
            self.assertNotEqual(identities['cold'][0]['after'], identities['cold'][1]['after'])
