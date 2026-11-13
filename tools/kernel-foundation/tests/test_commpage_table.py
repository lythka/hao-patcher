"""IntelCommpage's load-time address table: commpage_build.py's Python layout (table_bytes, read_table, stamp_table)
and src/commpage_table.h's C layout are the same bytes.
python3.14 -I -B -m unittest discover -s tools/kernel-foundation/tests

With KERNEL_FOUNDATION_COMMPAGE_ARTIFACT set to a built commpage artifact (store/artifacts/commpage-*), the built
kext's __DATA,__ic_table is build/commpage-table.bin and no table address is anywhere else in the kext."""
import json
import os
from pathlib import Path
import runpy
import shutil
import struct
import subprocess
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
UNIT = HERE.parent
F = runpy.run_path(str(UNIT/'commpage_build.py'))
LAYOUT = F['table_layout'](['_printf', '_PE_state', '_user_trap'], ['_commPagePtr32', '_commPagePtr64'],
                           ['_commpage32_map'], ['hook_a', 'hook_b', 'hook_c'])
VALUES = dict(kernel_uuid=bytes(range(16, 32)), kernel_header=0xffffff80002f0000, iosurface_header=0xffffff8002c00000,
              iosurface_set_value=0xffffff8002c1a0d6,
              symbols={'_printf': 0xffffff8000389590, '_PE_state': 0xffffff80013f78c0, '_user_trap': 0xffffff80004ee480},
              alias_slots=[0xffffff8001393f88, 0xffffff8001393f90], map_slots=[0xffffff8000221eb8],
              hook_sites=[0xffffff800048c290, 0xffffff800048c1f0, 0xffffff800048c330],
              hook_original=[bytes(range(i, i+14)) for i in (0, 20, 40)])


def have_clang():
    return shutil.which('xcrun') is not None and subprocess.run(['xcrun', '--find', 'clang++'],
                                                                capture_output=True).returncode == 0


class Python(unittest.TestCase):
    def test_round_trip_and_size(self):
        data = F['table_bytes'](LAYOUT, VALUES)
        self.assertEqual(len(data), F['table_size'](LAYOUT))
        self.assertEqual(len(data) % 8, 0)
        self.assertEqual(F['read_table'](data, LAYOUT), VALUES)
        self.assertEqual(struct.unpack_from('<III', data), (0x42544349, 1, len(data)))

    def test_refusals(self):
        data = F['table_bytes'](LAYOUT, VALUES)
        with self.assertRaisesRegex(ValueError, 'magic/version'):
            F['read_table'](data[:4]+struct.pack('<I', 2)+data[8:], LAYOUT)
        with self.assertRaisesRegex(ValueError, 'layout differ'):
            F['read_table'](data, dict(LAYOUT, hooks=['hook_a', 'hook_c', 'hook_b']))
        with self.assertRaisesRegex(ValueError, 'do not fill'):
            F['table_bytes'](LAYOUT, dict(VALUES, hook_sites=VALUES['hook_sites'][:2]))


@unittest.skipUnless(have_clang(), 'needs xcrun clang++')
class C(unittest.TestCase):
    def test_compiled_table_is_the_python_bytes(self):
        """A translation unit defining the table as commpage.cpp does, from the lines commpage_build.py writes into
        commpage_abi.hpp: its __DATA,__ic_table is table_bytes(), and stamp_table rewrites it in place."""
        with tempfile.TemporaryDirectory() as temporary:
            t = Path(temporary)
            (t/'commpage_abi.hpp').write_text('#include <stdint.h>\n'+'\n'.join(F['table_header_lines'](LAYOUT, VALUES))+'\n')
            (t/'unit.cpp').write_text('#include "commpage_abi.hpp"\n#include "commpage_table.h"\nextern "C" {\n'
                                      '__attribute__((used, section("__DATA,__ic_table"), aligned(8)))\n'
                                      'IntelCommpageTable IntelCommpage_table = IC_TABLE_REFERENCE;\n}\n')
            subprocess.run(['xcrun', 'clang++', '-target', 'x86_64-apple-macos10.13', '-std=c++17', '-O2',
                            '-I'+str(t), '-I'+str(UNIT/'src'), '-c', str(t/'unit.cpp'), '-o', str(t/'unit.o')],
                           check=True, capture_output=True)
            binary = (t/'unit.o').read_bytes()
        offset, size = F['find_table'](binary)
        self.assertEqual(binary[offset:offset+size], F['table_bytes'](LAYOUT, VALUES))
        moved = dict(VALUES, symbols={k: v+0x10000 for k, v in VALUES['symbols'].items()})
        stamped = F['stamp_table'](binary, LAYOUT, F['table_bytes'](LAYOUT, moved))
        self.assertEqual(F['read_table'](stamped[offset:offset+size], LAYOUT), moved)
        self.assertEqual(stamped[:offset]+stamped[offset+size:], binary[:offset]+binary[offset+size:])
        with self.assertRaisesRegex(ValueError, 'compiled for kernel'):
            F['stamp_table'](binary, LAYOUT, F['table_bytes'](LAYOUT, dict(VALUES, kernel_uuid=bytes(16))))


@unittest.skipUnless(os.environ.get('KERNEL_FOUNDATION_COMMPAGE_ARTIFACT'), 'KERNEL_FOUNDATION_COMMPAGE_ARTIFACT not set')
class Built(unittest.TestCase):
    def test_kext_reads_every_collection_address_from_its_table(self):
        artifact = Path(os.environ['KERNEL_FOUNDATION_COMMPAGE_ARTIFACT'])
        binary = (artifact/'IntelCommpage.kext/Contents/MacOS/IntelCommpage').read_bytes()
        table = (artifact/'build/commpage-table.bin').read_bytes()
        layout = json.loads((artifact/'build/commpage-table-layout.json').read_text())
        offset, size = F['find_table'](binary)
        self.assertEqual(binary[offset:offset+size], table)
        v = F['read_table'](table, layout)
        words = [*v['symbols'].values(), *v['alias_slots'], *v['map_slots'], *v['hook_sites'], v['kernel_header'],
                 v['iosurface_header'], v['iosurface_set_value']]
        rest = binary[:offset]+binary[offset+size:]
        self.assertEqual([hex(w) for w in words if struct.pack('<Q', w) in rest], [])


if __name__ == '__main__':
    unittest.main()
