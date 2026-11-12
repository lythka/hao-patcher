"""metal-metalold: the declared edit changes exactly the two bytes OCLP's MetalOld changes, and nothing else.
python3.14 -I -B -m unittest discover -s patches/metal-metalold/tests

METALOLD_22D68=PATH (the row's MetalOld.dylib) runs it on the real cut."""
import os
from pathlib import Path
import sys
import unittest

PATCH = Path(__file__).resolve().parents[1]
TOP = PATCH.parents[1]
sys.path.insert(0, str(TOP / 'tools/edit-engine/src'))
sys.path.insert(0, str(TOP / 'tools/edit-engine/tests'))
import engine   # noqa: E402
import spec as S   # noqa: E402
import fixture   # noqa: E402

SPEC = S.load(PATCH / 'edits/metalold.json')
CONTEXT = bytes.fromhex('4889c34885c074724d85e474')


def changed(a, b):
    return [i for i in range(len(a)) if a[i] != b[i]]


class Declaration(unittest.TestCase):
    def test_spec(self):
        self.assertEqual(S.validate(SPEC), [])
        self.assertEqual(SPEC['sign'], {'mode': 'adhoc', 'preserve': ['identifier']})

    def test_synthetic(self):
        data = fixture.dylib(size=0x3000, text_at=0x1000, text_size=0x1000, code={0x1400: CONTEXT})
        out, rec = engine.apply(SPEC, data)
        self.assertEqual(changed(data, out), [0x1406, 0x1407])
        self.assertEqual(out[0x1406:0x1408], b'\x90\x90')

    @unittest.skipUnless(os.environ.get('METALOLD_22D68'), 'set METALOLD_22D68 to the row\'s MetalOld.dylib')
    def test_real_cut(self):
        data = Path(os.environ['METALOLD_22D68']).read_bytes()
        out, rec = engine.apply(SPEC, data)
        self.assertEqual(changed(data, out), [0xd94a1, 0xd94a2])
        self.assertEqual(rec['expected'], 'match')


if __name__ == '__main__':
    unittest.main()
