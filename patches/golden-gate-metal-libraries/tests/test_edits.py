"""patch.json "files" declares what the kc-hook installs: install_libraries.py's repaired metallibs, and the two
runtime files the hook copies. All are prebuilt and installed whole (the hook edits nothing in place), so each is
an origin entry from its row, not an edit spec; metallib.rewrite is therefore not used. No byte test exists for the
metallib rewrite itself (cache/metallib/make, its own tests).
python3.14 -I -B -m unittest discover -s patches/golden-gate-metal-libraries/tests

Always: the declared paths and rows match install_libraries.py, kc-hook.sh and compiler_mask.py. With real inputs:
  METALLIB_REPAIRS_ROW   a metallib_repairs_golden_gate artifact folder: its shipped libraries (SOURCES, SHA256SUMS
                         and the files on disk, as install_libraries.plan reads them) must all be declared; for
                         the 26B131 artifact the set must equal the declared libraries
  METALLIB_RUNTIME_ROW   the metal_quad_runtime_golden_gate artifact folder: both declared runtime files are in it
  METALLIB_ICBE_SOURCE   the iCBE.dylib it was made from: compiler_mask.repair plus ad-hoc codesign (on a temp copy)
                         gives exactly the row's iCBE.dylib"""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

PATCH = Path(__file__).resolve().parents[1]
TOP = PATCH.parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


install = load('install_libraries', PATCH/'src/install_libraries.py')
sys.path.insert(0, str(TOP/'cache/metallib/lib'))
import compiler_mask   # noqa: E402

PATCH_JSON = json.loads((PATCH/'patch.json').read_text())
FILES = {f['path']: f for f in PATCH_JSON['files']}
HOOK = (PATCH/'src/kc-hook.sh').read_text()
HOOK_PATHS = re.findall(r'^relative=(\S+)$', HOOK, re.M)
RUNTIME = [p for p, f in FILES.items() if f['origin']['row'] == 'metal_quad_runtime_golden_gate']
LIBRARIES = {p for p, f in FILES.items() if f['origin']['row'] == 'metallib_repairs_golden_gate'}


def sha(data):
    return hashlib.sha256(data).hexdigest()


class Declaration(unittest.TestCase):
    def test_every_file_is_a_whole_file_from_a_patch_row(self):
        self.assertEqual(len(FILES), len(PATCH_JSON['files']))
        for f in FILES.values():
            self.assertNotIn('edits', f)
            self.assertEqual(f['origin']['kind'], 'hao')
            self.assertIn(f['origin']['row'], PATCH_JSON['inputs'])
            self.assertTrue(f['origin'].get('note'))

    def test_runtime_files_are_the_ones_the_hook_installs(self):
        self.assertEqual(sorted(RUNTIME), sorted(HOOK_PATHS))
        self.assertIn(compiler_mask.RELATIVE, RUNTIME)

    def test_hook_hands_the_libraries_to_install_libraries_py(self):
        self.assertIn('install_libraries.py', HOOK)
        self.assertIn('inputs/metallib_repairs_golden_gate', HOOK)
        self.assertEqual(LIBRARIES | set(RUNTIME), set(FILES))

    def test_install_libraries_plan_installs_exactly_the_declared_libraries(self):
        """A synthetic System and artifact with one library per declared path: plan() installs all of them."""
        import plistlib
        with tempfile.TemporaryDirectory() as tmp:
            system, artifact = Path(tmp)/'system', Path(tmp)/'artifact'
            lines_s, lines_o = [], []
            for rel in sorted(LIBRARIES):
                for root, data in ((system, b'stock ' + rel.encode()), (artifact, b'repaired ' + rel.encode())):
                    (root/rel).parent.mkdir(parents=True, exist_ok=True)
                    (root/rel).write_bytes(data)
                lines_s.append('%s  %s\n' % (sha(b'stock ' + rel.encode()), rel))
                lines_o.append('%s  %s\n' % (sha(b'repaired ' + rel.encode()), rel))
            (artifact/'SOURCES').write_text(''.join(lines_s))
            (artifact/'SHA256SUMS').write_text(''.join(lines_o))
            (artifact/'build.json').write_text(json.dumps({'system_build': '26B131'}))
            version = system/'System/Library/CoreServices/SystemVersion.plist'
            version.parent.mkdir(parents=True)
            version.write_bytes(plistlib.dumps({'ProductBuildVersion': '26B131'}))
            self.assertEqual({r for r, _ in install.plan(system, artifact)}, LIBRARIES)


@unittest.skipUnless(os.environ.get('METALLIB_REPAIRS_ROW'), 'set METALLIB_REPAIRS_ROW')
class RealRepairs(unittest.TestCase):
    def test_shipped_libraries_are_declared(self):
        artifact = Path(os.environ['METALLIB_REPAIRS_ROW'])
        sources, outputs = install.listing(artifact/'SOURCES'), install.listing(artifact/'SHA256SUMS')
        shipped = {str(p.relative_to(artifact)) for p in (artifact/'System').rglob('*.metallib')}
        self.assertEqual(set(sources), set(outputs))
        self.assertEqual(set(sources), shipped)
        self.assertLessEqual(shipped, LIBRARIES)
        for rel, expected in outputs.items():
            self.assertEqual(sha((artifact/rel).read_bytes()), expected)
        if json.loads((artifact/'build.json').read_text())['system_build'] == '26B131':
            self.assertEqual(shipped, LIBRARIES)


@unittest.skipUnless(os.environ.get('METALLIB_RUNTIME_ROW'), 'set METALLIB_RUNTIME_ROW')
class RealRuntime(unittest.TestCase):
    def setUp(self):
        self.row = Path(os.environ['METALLIB_RUNTIME_ROW'])

    def test_declared_runtime_files_are_in_the_row(self):
        sums = install.listing(self.row/'SHA256SUMS')
        self.assertEqual(set(sums), set(RUNTIME))
        for rel, expected in sums.items():
            self.assertEqual(sha((self.row/rel).read_bytes()), expected)

    @unittest.skipUnless(os.environ.get('METALLIB_ICBE_SOURCE'), 'set METALLIB_ICBE_SOURCE')
    def test_icbe_is_compiler_mask_repair_plus_adhoc_signature(self):
        repaired, report = compiler_mask.repair(Path(os.environ['METALLIB_ICBE_SOURCE']).read_bytes())
        self.assertEqual([c['after'] for c in report['changes']], [4, 0])
        with tempfile.TemporaryDirectory() as tmp:
            binary = Path(tmp)/'iCBE.dylib'
            binary.write_bytes(repaired)
            subprocess.run(['codesign', '-f', '-s', '-', str(binary)], check=True, capture_output=True)
            self.assertEqual(binary.read_bytes(), (self.row/compiler_mask.RELATIVE).read_bytes())


if __name__ == '__main__':
    unittest.main()
