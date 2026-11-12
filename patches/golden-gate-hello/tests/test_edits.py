"""edits/metal.json declares what src/edit-metal.py does: the engine's output is byte-identical to the script's.
python3.14 -I -B -m unittest discover -s patches/golden-gate-hello/tests

Always runs on a synthetic wrapper with the same layout. With HELLO_OCLP_METAL=PATH (OCLP's Metal wrapper,
PatcherSupportPkg 2.0.5) it also runs on the real file and checks the spec's recorded output hash."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

PATCH = Path(__file__).resolve().parents[1]
TOP = PATCH.parents[1]
sys.path.insert(0, str(TOP / 'tools/edit-engine/src'))
sys.path.insert(0, str(TOP / 'tools/edit-engine/tests'))
import engine   # noqa: E402
import spec as S   # noqa: E402
import fixture   # noqa: E402

SPEC = S.load(PATCH / 'edits/metal.json')


def script_output(data):
    d = Path(tempfile.mkdtemp())
    (d / 'in').write_bytes(data)
    r = subprocess.run([sys.executable, '-I', str(PATCH / 'src/edit-metal.py'), str(d / 'in'), str(d / 'out')],
                       capture_output=True, text=True)
    if r.returncode:
        raise AssertionError(r.stderr)
    return (d / 'out').read_bytes()


class Declaration(unittest.TestCase):
    def test_spec_follows_the_format(self):
        self.assertEqual(S.validate(SPEC), [])
        self.assertEqual(SPEC['path'], 'System/Library/Frameworks/Metal.framework/Versions/A/Metal')
        files = json.loads((PATCH / 'patch.json').read_text())['files']
        self.assertIn({'path': SPEC['path'], 'edits': 'edits/metal.json'}, files)

    def test_same_bytes_as_edit_metal_on_a_synthetic_wrapper(self):
        wrapper = fixture.dylib(
            size=0xd000, text_at=0xbb0, text_size=0xb000, ident=b'/System/Library/Frameworks/Metal.framework/Metal',
            symbols={'_fake_LCAUtilities_shouldSkipHello': 0xb820, '_helloSetup': 0xb860},
            code={0xb855: bytes.fromhex('b0010fbec04883c4105dc3'), 0xb87f: bytes.fromhex('3c007448488d3d')})
        out, record = engine.apply(SPEC, wrapper, repo=str(TOP))
        self.assertEqual(out, script_output(wrapper))
        self.assertEqual([e['where'][0].split()[0] for e in record['edits']][:2], ['0xb855', '0xb87f'])

    @unittest.skipUnless(os.environ.get('HELLO_OCLP_METAL'), 'set HELLO_OCLP_METAL to OCLP\'s Metal wrapper')
    def test_same_bytes_as_edit_metal_on_oclps_wrapper(self):
        data = Path(os.environ['HELLO_OCLP_METAL']).read_bytes()
        out, record = engine.apply(SPEC, data, repo=str(TOP))
        self.assertEqual(out, script_output(data))
        self.assertEqual(record['expected'], 'match')


if __name__ == '__main__':
    unittest.main()
