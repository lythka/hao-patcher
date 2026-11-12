"""edits/driver.json declares what src/add_weak_dylib.py does for this recipe: the engine's output is byte-identical
to the script's (before the hook's `codesign -f -s -`).
python3.14 -I -B -m unittest discover -s patches/golden-gate-metal-framebuffer/tests

Real input: the driver after metalold-selectors, i.e. add_weak_dylib.py's output on OCLP's driver (sha256 fb453c0a...).
Always runs on a synthetic dylib with the same layout. With GOLDEN_GATE_METAL_FRAMEBUFFER_INPUT=PATH (the driver as metalold-selectors leaves it) it also runs on the real file
and checks the spec's recorded output hash."""
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

SPEC = S.load(PATCH / 'edits/driver.json')
DYLIB = '/usr/lib/libmetal-framebuffer.dylib'
DRIVER = 'System/Library/Extensions/AppleIntelHD4000GraphicsMTLDriver.bundle/Contents/MacOS/AppleIntelHD4000GraphicsMTLDriver'


def script_output(data):
    d = Path(tempfile.mkdtemp())
    (d / 'in').write_bytes(data)
    r = subprocess.run([sys.executable, '-I', str(PATCH / 'src/add_weak_dylib.py'), str(d / 'in'), str(d / 'out'), DYLIB],
                       capture_output=True, text=True)
    if r.returncode:
        raise AssertionError(r.stderr)
    return (d / 'out').read_bytes()


class Declaration(unittest.TestCase):
    def test_spec_follows_the_format(self):
        self.assertEqual(S.validate(SPEC), [])
        self.assertEqual(SPEC['path'], DRIVER)
        files = json.loads((PATCH / 'patch.json').read_text())['files']
        self.assertIn({'path': DRIVER, 'edits': 'edits/driver.json'}, files)
        op = SPEC['edits'][0]['args']
        self.assertEqual((op['path'], op['weak'], op['timestamp'], op.get('current_version', 0), op.get('compat_version', 0)),
                         (DYLIB, True, 2, 0, 0))

    def test_same_bytes_as_add_weak_dylib_on_a_synthetic_driver(self):
        driver = fixture.dylib(size=0x3000, text_at=0x1000, text_size=0x1000,
                               ident=b'/System/Library/Extensions/AppleIntelHD4000GraphicsMTLDriver.bundle/Contents/MacOS/AppleIntelHD4000GraphicsMTLDriver')
        # as the file is when this recipe runs: the selectors recipe's weak load command is already in it
        driver, _, _ = engine.macho.add_dylib(driver, '/usr/lib/libmetalold-selectors.dylib', weak=True)
        out, record = engine.apply(SPEC, driver, repo=str(TOP))
        self.assertEqual(out, script_output(driver))
        self.assertEqual(len(record['edits']), 1)

    @unittest.skipUnless(os.environ.get('GOLDEN_GATE_METAL_FRAMEBUFFER_INPUT'), 'set GOLDEN_GATE_METAL_FRAMEBUFFER_INPUT to the driver as metalold-selectors leaves it')
    def test_same_bytes_as_add_weak_dylib_on_the_real_driver(self):
        data = Path(os.environ['GOLDEN_GATE_METAL_FRAMEBUFFER_INPUT']).read_bytes()
        out, record = engine.apply(SPEC, data, repo=str(TOP))
        self.assertEqual(out, script_output(data))
        self.assertEqual(record['expected'], 'match')


if __name__ == '__main__':
    unittest.main()
