import importlib.util
from pathlib import Path
import struct
import tempfile
import unittest

spec=importlib.util.spec_from_file_location('selection',Path(__file__).resolve().parents[1]/'selection.py')
selection=importlib.util.module_from_spec(spec);spec.loader.exec_module(selection)

class Selection(unittest.TestCase):
    def test_pins_mixed_architectures_resources_and_missing_roots_remain_donors(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            for name in ('native','mixed','pinned','cached','resources'):
                (root/name).mkdir()
            for name in ('native','mixed','pinned'):
                (root/name/'intel').write_bytes(struct.pack('<II',0xfeedfacf,0x1000007))
            (root/'mixed/arm').write_bytes(struct.pack('<II',0xfeedfacf,0x100000c))
            req=dict(system_roots=['native','mixed','pinned','cached','resources','absent'],keep_donor_roots=['pinned'])
            choices=selection.target_supplied(req,root,['/cached/Library','/mixed/Library','/absent/Library'])
            self.assertEqual([c[0] for c in choices],['native','cached'])

    def test_fat64_stride_is_not_fat32_stride(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'fat';head=bytearray(72)
            struct.pack_into('>II',head,0,0xcafebabf,2)
            struct.pack_into('>I',head,8,0x100000c)
            struct.pack_into('>I',head,40,0x1000007)
            p.write_bytes(head)
            self.assertTrue(selection.x86(p))
            p.write_bytes(head[:40])
            with self.assertRaises(ValueError):selection.x86(p)
