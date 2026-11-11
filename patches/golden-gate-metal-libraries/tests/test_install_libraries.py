"""install_libraries.plan: a repaired library is installed only over the System library it was made from, or over
OCLP's copy when the System is the build it was made from.
Run: python3.14 -I -m unittest discover -s patches/golden-gate-metal-libraries/tests"""
import hashlib
import importlib.util
import json
from pathlib import Path
import plistlib
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('install_libraries', Path(__file__).resolve().parents[1]/'src/install_libraries.py')
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)
A = 'System/Library/PrivateFrameworks/A.framework/Versions/A/Resources/default.metallib'
B = 'System/Library/PrivateFrameworks/B.framework/Versions/A/Resources/default.metallib'


def sha(data):
    return hashlib.sha256(data).hexdigest()


class Plan(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.system, self.artifact = root/'system', root/'artifact'
        for rel, data in ((A, b'stock-a'), (B, b'oclp-b')):
            (self.system/rel).parent.mkdir(parents=True)
            (self.system/rel).write_bytes(data)
            (self.artifact/rel).parent.mkdir(parents=True)
            (self.artifact/rel).write_bytes(b'repaired-'+data)
        version = self.system/'System/Library/CoreServices/SystemVersion.plist'
        version.parent.mkdir(parents=True)
        version.write_bytes(plistlib.dumps({'ProductBuildVersion': '26B131'}))
        (version.parent/'OpenCore-Legacy-Patcher.plist').write_bytes(plistlib.dumps(
            {'Graphics': {'Merge System Volume': {'/'+str(Path(B).parent): {'default.metallib': '26.0-3802'}}}}))
        (self.artifact/'SHA256SUMS').write_text(''.join(f'{sha((self.artifact/r).read_bytes())}  {r}\n' for r in (A, B)))
        (self.artifact/'SOURCES').write_text(f'{sha(b"stock-a")}  {A}\n{sha(b"stock-b")}  {B}\n')
        (self.artifact/'build.json').write_text(json.dumps({'system_build': '26B131'}))

    def tearDown(self):
        self.tmp.cleanup()

    def test_matching_source_and_oclp_copy_of_the_same_build(self):
        self.assertEqual(tool.plan(self.system, self.artifact),
                         [(A, 'source matches'), (B, "replaces OCLP's copy; made from 26B131")])

    def test_other_build_stops(self):
        (self.artifact/'build.json').write_text(json.dumps({'system_build': '26A428'}))
        with self.assertRaisesRegex(SystemExit, r'MISSING/unsupported: /System/.*B\.framework.*build 26A428, System 26B131'):
            tool.plan(self.system, self.artifact)

    def test_changed_source_stops(self):
        (self.system/A).write_bytes(b'other-a')
        with self.assertRaisesRegex(SystemExit, r'MISSING/unsupported: /System/.*A\.framework.* is [0-9a-f]{64}; the repair'):
            tool.plan(self.system, self.artifact)

    def test_unrecorded_artifact_stops(self):
        (self.artifact/'SOURCES').unlink()
        with self.assertRaisesRegex(SystemExit, 'has no SOURCES'):
            tool.plan(self.system, self.artifact)
        (self.artifact/'SOURCES').write_text(f'{sha(b"stock-a")}  {A}\n')
        with self.assertRaisesRegex(SystemExit, 'differ'):
            tool.plan(self.system, self.artifact)


if __name__ == '__main__':
    unittest.main()
