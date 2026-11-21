import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from dyld_rebuild.witness_template import emit, produce
from dyld_rebuild.witness_runtime import developer_link
from dyld_rebuild.exports import Refusal


@unittest.skipUnless(os.environ.get('SWIFT_WITNESS_RUNTIME_TOOLCHAIN'), 'requires producer toolchain and Xcode')
class WitnessTemplateTests(unittest.TestCase):
    def test_python_placement_matches_linker_and_refuses_wrong_object_or_placement(self):
        toolchain = Path(os.environ['SWIFT_WITNESS_RUNTIME_TOOLCHAIN'])
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            produce(toolchain, root)
            template = root/'witness-template.json'
            for i,(code,data) in enumerate(((0x100000,0x110000),(0x200000000,0x200120000),
                                           (0x7ff86235f000,0x7ff863415000))):
                out = root/str(i);out.mkdir()
                self.assertEqual(emit(template,toolchain/'runtime.o',code,data)[:2],
                                 developer_link(toolchain,code,data,out)[:2])
            with self.assertRaisesRegex(Refusal, 'out of range'):
                emit(template,toolchain/'runtime.o',0x100000,0x100000000)
            wrong = root/'wrong.o';wrong.write_bytes(b'wrong')
            with self.assertRaisesRegex(Refusal, 'object differs'):
                emit(template,wrong,0x100000,0x110000)
