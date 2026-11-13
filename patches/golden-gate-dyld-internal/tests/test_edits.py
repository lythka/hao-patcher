"""edits/dyld.json declares what src/edit_dyld.py does; both refuse what they must."""
import os
from pathlib import Path
import runpy
import struct
import sys
import unittest

PATCH = Path(__file__).resolve().parents[1]
TOP = PATCH.parents[1]
sys.path.insert(0, str(TOP / 'tools/edit-engine/src'))
import engine   # noqa: E402
import spec as S   # noqa: E402

SPEC = S.load(PATCH / 'edits/dyld.json')
ED = runpy.run_path(str(PATCH / 'src/edit_dyld.py'))
LP = runpy.run_path(str(TOP / 'tools/hao/src/livepatch.py'))
FUNC = bytes.fromhex('554889e5bf10000000e84475feff85c00f94c05dc3')


def real_dyld():
    if os.environ.get('HAO_DYLD'):
        return Path(os.environ['HAO_DYLD'])
    try:
        p = Path(runpy.run_path(str(TOP / 'pipeline/lock.py'))['path'](str(TOP / 'pipeline/inputs/golden/26B131'),
                                                                        'hao_runtime')) / 'replace/dyld'
        return p if p.is_file() else None
    except Exception:
        return None


def thin(body):
    """A minimal thin x86_64 Mach-O header followed by BODY (edit_dyld only reads the header's cputype)."""
    return b'\xcf\xfa\xed\xfe' + struct.pack('<I', 0x01000007) + b'\0' * 24 + body


class Script(unittest.TestCase):
    def test_spec(self):
        self.assertEqual(S.validate(SPEC), [])
        e = SPEC['edits'][0]
        self.assertEqual(bytes.fromhex(e['replace']), LP['DYLD_INTERNAL_EDITED'])   # what hao patch and hao-opencore read
        self.assertEqual(bytes.fromhex(e['replace'])[9:14], ED['NEW'])

    def test_one_site_edited(self):
        out, at = ED['edit'](thin(b'\x90' * 40 + FUNC + b'\x90' * 40))
        self.assertIn(LP['DYLD_INTERNAL_EDITED'], out)
        self.assertEqual(len(out), 32 + 40 + len(FUNC) + 40)

    def test_refusals(self):
        for body, why in ((b'\x90' * 64, '0 matches'), (FUNC + FUNC, '2 matches'),
                          (LP['DYLD_INTERNAL_EDITED'], 'already edited')):
            with self.assertRaises(SystemExit) as x:
                ED['edit'](thin(body))
            self.assertIn(why, str(x.exception))
        with self.assertRaises(SystemExit):
            ED['edit'](b'not a mach-o')

    @unittest.skipUnless(real_dyld(), 'set HAO_DYLD, or have the hao_runtime row resolve')
    def test_engine_and_script_agree_on_the_real_dyld(self):
        data = real_dyld().read_bytes()
        script, at = ED['edit'](data)
        nosign = dict(SPEC, sign=None)
        nosign.pop('sign')
        out, record = engine.apply(nosign, data, repo=str(TOP))
        self.assertEqual(out, script)
        self.assertEqual(sum(a != b for a, b in zip(data, script)), 5 - sum(
            a == b for a, b in zip(data[at:at + 5], ED['NEW'])))


if __name__ == '__main__':
    unittest.main()
