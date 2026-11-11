import importlib.util
from pathlib import Path
import struct
import sys
import unittest
from compat_fixture import fixture, reader, BASE
ROOT=Path(__file__).resolve().parents[1]/'src'
for name in ('legacy_trackpad','core_routine'):
    spec=importlib.util.spec_from_file_location(name,ROOT/(name+'.py'))
    mod=importlib.util.module_from_spec(spec);sys.modules[name]=mod;spec.loader.exec_module(mod)
routine=sys.modules['core_routine']

class CoreRoutineTest(unittest.TestCase):
    def test_relocated_call_preserves_return_and_tail_jumps_to_original_api(self):
        for shift in (0,37):
            data,sites=fixture('CoreRoutine',shift)
            cave,call=routine.checked_edits(reader(data),BASE)
            self.assertEqual(call[0],BASE+sites[0]+8)
            self.assertEqual(call[2][0],0xe8)
            self.assertEqual(call[0]+5+struct.unpack('<i',call[2][1:])[0],cave[0])
            self.assertEqual(cave[2][:6],bytes.fromhex('c70600000000'))
            self.assertEqual(cave[0]+11+struct.unpack('<i',cave[2][7:])[0],BASE+0xf000)

    def test_missing_guard_occupied_padding_and_nonstub_call_refuse(self):
        for which in ('guard','padding','stub','header'):
            data,sites=fixture('CoreRoutine')
            offset={'guard':sites[1],'padding':0x10f0,'stub':sites[0]+12,'header':4}[which]
            data[offset]^=1
            with self.subTest(which=which),self.assertRaises(ValueError):
                routine.checked_edits(reader(data),BASE)
