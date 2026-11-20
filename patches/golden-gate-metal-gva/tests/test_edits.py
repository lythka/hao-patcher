"""edits/applegva.json declares what src/edit-gva.py does: the engine's output is byte-identical to the script's.
python3.14 -I -B -m unittest discover -s patches/golden-gate-metal-gva/tests

Always runs on a synthetic dylib with the same layout. With GVA_OCLP_APPLEGVA=PATH (OCLP's AppleGVA, PatcherSupportPkg
2.0.5, 11.7.10) it also runs on the real file and checks the spec's recorded output hash."""
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

SPEC = S.load(PATCH / 'edits/applegva.json')


def script_output(data):
    d = Path(tempfile.mkdtemp())
    (d / 'in').write_bytes(data)
    r = subprocess.run([sys.executable, '-I', str(PATCH / 'src/edit-gva.py'), str(d / 'in'), str(d / 'out')],
                       capture_output=True, text=True)
    if r.returncode:
        raise AssertionError(r.stderr)
    return (d / 'out').read_bytes()


class Declaration(unittest.TestCase):
    def test_spec_follows_the_format(self):
        self.assertEqual(S.validate(SPEC), [])
        files = json.loads((PATCH / 'patch.json').read_text())['files']
        entry = next(f for f in files if f['path'] == SPEC['path'])
        self.assertEqual(entry['edits'], 'edits/applegva.json')
        self.assertEqual(entry['install'], {'mode': '0755', 'owner': 'root:wheel', 'flags': ['restricted']})   # as the hook installs it

    def test_same_bytes_as_edit_gva_on_a_synthetic_dylib(self):
        lib = fixture.dylib(
            size=0x6000, text_at=0xbb0, text_size=0x5000,
            ident=b'/System/Library/PrivateFrameworks/AppleGVA.framework/Versions/A/AppleGVA',
            symbols={'_AVFJPG_GetJPEGDecodeAvailability': 0x415f},
            code={0x415f: bytes.fromhex('554889e54157415641554154')})
        out, record = engine.apply(SPEC, lib, repo=str(TOP))
        self.assertEqual(out, script_output(lib))
        self.assertEqual(record['edits'][0]['where'][0].split()[0], '0x415f')

    @unittest.skipUnless(os.environ.get('GVA_OCLP_APPLEGVA'), "set GVA_OCLP_APPLEGVA to OCLP's AppleGVA")
    def test_same_bytes_as_edit_gva_on_oclps_applegva(self):
        data = Path(os.environ['GVA_OCLP_APPLEGVA']).read_bytes()
        out, record = engine.apply(SPEC, data, repo=str(TOP))
        self.assertEqual(out, script_output(data))
        self.assertEqual(record['expected'], 'match')
