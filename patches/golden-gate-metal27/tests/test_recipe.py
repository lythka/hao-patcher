"""golden-gate-metal27: the declaration, the installer's refusals, and the pack.
python3.14 -I -B -m unittest discover -s patches/golden-gate-metal27/tests

Synthetic always (install.py --check on a folder System and a folder artifact; nothing is installed). With
GOLDEN_GATE_METAL27_ARTIFACT=DIR (the metal27_installed row's folder, a `tools/metal-adapter/make.py installed`
result): its build.json matches its files and names the pinned cut, and its Metal is the laptop bundle's file. With
GOLDEN_GATE_METAL27_OCLP_METAL=FILE (OCLP's wrapper from the hardware artifact) it is a base install.py accepts."""
import hashlib
import json
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
RECIPE = HERE.parent
TOP = RECIPE.parents[1]
INSTALL = runpy.run_path(str(RECIPE / 'src' / 'install.py'))
MAKE = runpy.run_path(str(TOP / 'tools' / 'metal-adapter' / 'make.py'))
PATCH = json.loads((RECIPE / 'patch.json').read_text())
BASES = json.loads((RECIPE / 'src' / 'bases.json').read_text())
METAL = INSTALL['METAL']
BUNDLE_METAL = 'e0d38ca0a7191c1636b19feec9d53f8a7aaa75356284644457aba401de5535de'   # laptop runs r2-r4


def sha(b):
    return hashlib.sha256(b).hexdigest()


class Declaration(unittest.TestCase):
    def test_files_are_the_four_the_builder_makes_in_install_order(self):
        self.assertEqual([f['path'] for f in PATCH['files']], INSTALL['ORDER'])
        self.assertEqual(sorted(INSTALL['ORDER']), sorted(MAKE['INSTALLED']))
        for f in PATCH['files']:
            self.assertEqual(f['origin']['row'], 'metal27_installed')
            self.assertEqual(f['install'], {'mode': '0755', 'owner': 'root:wheel', 'flags': ['restricted']})

    def test_dev_only_in_one_opt_in_pack_and_no_default_set(self):
        self.assertEqual((PATCH['status'], PATCH['validated']), ('dev', False))
        pack = json.loads((TOP / 'packs' / 'metal27-trial.json').read_text())
        self.assertEqual((pack['recipes'], pack['opt_in'], pack['phase']), (['golden-gate-metal27'], True, 'live-capable'))
        for s in ('golden-gate-26B131', 'golden-gate', 'dev-bs-test', 'recovery'):
            text = (TOP / 'pipeline' / 'sets' / (s + '.txt')).read_text()
            self.assertNotIn('golden-gate-metal27', [l.split('#')[0].strip() for l in text.splitlines()], s)

    def test_hello_edited_wrapper_is_an_accepted_base(self):
        spec = json.loads((TOP / 'patches/golden-gate-hello/edits/metal.json').read_text())
        oclp, edited = next(iter(spec['expect'].items()))
        self.assertIn(oclp, BASES[METAL])
        self.assertIn(edited, BASES[METAL])

    def test_hook_parses(self):
        subprocess.run(['/bin/sh', '-n', str(RECIPE / 'src' / 'kc-hook.sh')], check=True)


class Check(unittest.TestCase):
    """install.py --check on synthetic folders, with a bases table for synthetic bytes."""
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.art, self.sys = self.d / 'artifact', self.d / 'system'
        files = {}
        for i, p in enumerate(INSTALL['ORDER']):
            data = b'\xcf\xfa\xed\xfe artifact %d' % i
            (self.art / 'root' / p).parent.mkdir(parents=True, exist_ok=True)
            (self.art / 'root' / p).write_bytes(data)
            files[p] = sha(data)
        (self.art / 'build.json').write_text(json.dumps({'tool': 'tools/metal-adapter/make.py installed', 'files': files}))
        self.files = files
        self.wrapper = b'\xcf\xfa\xed\xfe wrapper'
        self.bases = {METAL: {sha(self.wrapper): 'synthetic wrapper'}, 'needs': BASES['needs']}
        for p in BASES['needs']:
            (self.sys / p).parent.mkdir(parents=True, exist_ok=True)
            (self.sys / p).write_bytes(b'oclp')
        (self.sys / METAL).parent.mkdir(parents=True, exist_ok=True)
        (self.sys / METAL).write_bytes(self.wrapper)

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def check(self):
        return INSTALL['check'](self.sys, self.art, self.bases)

    def refused(self, text):
        with self.assertRaises(ValueError) as e:
            self.check()
        self.assertIn(text, str(e.exception))

    def test_accepts_the_wrapper(self):
        self.assertEqual(self.check()['files'], self.files)

    def test_accepts_a_system_that_already_has_it(self):
        for p in INSTALL['ORDER']:
            (self.sys / p).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(self.art / 'root' / p, self.sys / p)
        self.check()

    def test_refuses_another_metal(self):
        (self.sys / METAL).write_bytes(b'something else')
        self.refused('not a Metal this trial replaces')

    def test_refuses_an_unknown_file_at_a_standin_path(self):
        p = self.sys / INSTALL['ORDER'][0]
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b'not ours')
        self.refused('exists and is not the artifact')

    def test_refuses_without_the_oclp_compilers(self):
        (self.sys / BASES['needs'][1]).unlink()
        self.refused('MISSING: /System/Library/PrivateFrameworks/MTLCompiler.framework/Versions/31001/MTLCompiler')

    def test_refuses_a_changed_artifact(self):
        (self.art / 'root' / METAL).write_bytes(b'changed')
        self.refused('artifact file differs from build.json')

    def test_refuses_the_running_system(self):
        with self.assertRaises(ValueError):
            INSTALL['check']('/', self.art, self.bases)


@unittest.skipUnless(os.environ.get('GOLDEN_GATE_METAL27_ARTIFACT'), 'set GOLDEN_GATE_METAL27_ARTIFACT to the row folder')
class Artifact(unittest.TestCase):
    def setUp(self):
        self.art = Path(os.environ['GOLDEN_GATE_METAL27_ARTIFACT'])
        self.record = json.loads((self.art / 'build.json').read_text())

    def test_build_record(self):
        self.assertEqual(sorted(self.record['files']), sorted(INSTALL['ORDER']))
        for p, digest in self.record['files'].items():
            self.assertEqual(sha((self.art / 'root' / p).read_bytes()), digest, p)
        self.assertEqual(self.record['files'][METAL], BUNDLE_METAL)
        self.assertEqual(self.record['inputs']['metal27_cut_sha256'], MAKE['INPUTS']['metal27']['cut_sha256'])
        self.assertNotIn('dirty', self.record['commit'] or 'dirty')

    @unittest.skipUnless(os.environ.get('GOLDEN_GATE_METAL27_OCLP_METAL'), 'set GOLDEN_GATE_METAL27_OCLP_METAL')
    def test_oclp_wrapper_is_a_base(self):
        self.assertIn(sha(Path(os.environ['GOLDEN_GATE_METAL27_OCLP_METAL']).read_bytes()), BASES[METAL])


if __name__ == '__main__':
    unittest.main()
