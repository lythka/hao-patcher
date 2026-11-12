"""edits/dss-shim.json declares what src/load_shim.py does to the display-state shim: the engine's output is
byte-identical to the script's. python3.14 -I -B -m unittest discover -s patches/golden-gate-windowserver-filter/tests

Always runs on a synthetic x86_64 dylib. With WINDOWSERVER_FILTER_DSS_SHIM=PATH (input row display_state_shim's
dss_shim.dylib, 26B131) it also runs on the real file, checks the spec's recorded output hash and that the file's
signature identifier is the one the spec re-signs with."""
import hashlib
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import unittest

PATCH = Path(__file__).resolve().parents[1]
TOP = PATCH.parents[1]
sys.path.insert(0, str(TOP / 'tools/edit-engine/src'))
sys.path.insert(0, str(TOP / 'tools/edit-engine/tests'))
import engine   # noqa: E402
import spec as S   # noqa: E402
import fixture   # noqa: E402

SPEC = S.load(PATCH / 'edits/dss-shim.json')
SCRIPT = runpy.run_path(str(PATCH / 'src/load_shim.py'))


def script_output(data):
    out = bytearray(data)
    SCRIPT['add'](out)
    return bytes(out)


class Declaration(unittest.TestCase):
    def test_spec_follows_the_format(self):
        self.assertEqual(S.validate(SPEC), [])
        self.assertEqual(SPEC['path'], 'usr/lib/dss_shim.dylib')
        files = json.loads((PATCH / 'patch.json').read_text())['files']
        self.assertIn({'path': SPEC['path'], 'edits': 'edits/dss-shim.json'}, files)
        edit, = SPEC['edits']
        self.assertEqual(edit['args']['path'].encode(), SCRIPT['DYLIB'])
        self.assertEqual((edit['op'], edit['args']), ('macho.add-dylib', {
            'path': '/usr/lib/windowserver_filter.dylib', 'weak': False, 'timestamp': 2,
            'current_version': 0x10000, 'compat_version': 0x10000}))
        self.assertEqual(SPEC['sign']['mode'], 'adhoc')

    def test_same_bytes_as_load_shim_on_a_synthetic_dylib(self):
        data = fixture.dylib(ident=b'/usr/lib/dss_shim.dylib')
        out, record = engine.apply(SPEC, data, repo=str(TOP))
        self.assertEqual(out, script_output(data))
        self.assertNotEqual(out, data)

    @unittest.skipUnless(os.environ.get('WINDOWSERVER_FILTER_DSS_SHIM'),
                         'set WINDOWSERVER_FILTER_DSS_SHIM to the 26B131 dss_shim.dylib')
    def test_same_bytes_as_load_shim_on_the_display_state_shim(self):
        path = Path(os.environ['WINDOWSERVER_FILTER_DSS_SHIM'])
        data = path.read_bytes()
        out, record = engine.apply(SPEC, data, repo=str(TOP))
        self.assertEqual(out, script_output(data))
        if hashlib.sha256(data).hexdigest() in SPEC['expect']:
            self.assertEqual(record['expected'], 'match')
        ident = subprocess.run(['codesign', '-dv', str(path)], capture_output=True, text=True).stderr
        self.assertIn('identifier', SPEC['sign']['preserve'])          # kept from the shim, as kc-hook.sh does
        self.assertRegex(ident, r'\nIdentifier=dss_shim-[0-9a-f]+\n')


if __name__ == '__main__':
    unittest.main()
