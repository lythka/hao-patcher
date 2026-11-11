import importlib.util
from pathlib import Path
import struct
import sys
import unittest
from compat_fixture import fixture,reader,BASE
ROOT=Path(__file__).resolve().parents[1]/'src'
for name in ('legacy_trackpad','endpoint_security'):
    spec=importlib.util.spec_from_file_location(name,ROOT/(name+'.py'))
    mod=importlib.util.module_from_spec(spec);sys.modules[name]=mod;spec.loader.exec_module(mod)
es=sys.modules['endpoint_security']

class EndpointSecurityTest(unittest.TestCase):
    def test_relocated_selectors_keep_removed_provider_slots_unused(self):
        for shift in (0,53):
            data,sites=fixture('libEndpointSecurity.dylib',shift)
            result=es.checked_edits(reader(data),BASE)
            self.assertEqual([r[0]-BASE for r in result],sites)
            self.assertEqual(len(result),31)
            for _,before,after,_ in result:
                old=struct.unpack('<I',before[1:])[0];new=struct.unpack('<I',after[1:])[0]
                self.assertEqual(new,7 if old==5 else old+3)
                self.assertNotIn(new,(5,6,8))
                if old>=25:self.assertGreaterEqual(new,28)

    def test_missing_operand_or_duplicate_shape_refuses_without_writing(self):
        data,sites=fixture('libEndpointSecurity.dylib')
        for site in sites:
            bad=bytearray(data);bad[site+1]^=1;original=bytes(bad)
            with self.assertRaises(ValueError):es.checked_edits(reader(bad),BASE)
            self.assertEqual(bytes(bad),original)
        data[0xc000:0xc080]=data[0x1200:0x1280]
        with self.assertRaises(ValueError):es.checked_edits(reader(data),BASE)
