"""Execute the production snapshot transaction and bind it to an optional real BootKC."""
from pathlib import Path
import os
import runpy
import subprocess
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
LAYOUT = runpy.run_path(str(HERE.parent/'iosurface_layout.py'))
KDK = Path(os.environ['DARWIN27_IOSURFACE_DONOR']) if os.environ.get('DARWIN27_IOSURFACE_DONOR') else None
KC = Path(os.environ['DARWIN27_BOOT_KC']) if os.environ.get('DARWIN27_BOOT_KC') else None


class Transaction(unittest.TestCase):
    def test_descriptor_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'harness'
            subprocess.run(['xcrun', 'clang++', '-std=c++17', '-Wall', '-Wextra', '-Werror',
                            str(HERE/'iosurface_snapshot_harness.cpp'), '-o', str(path)], check=True)
            subprocess.run(['codesign', '-s', '-', str(path)], check=True, capture_output=True)
            result = subprocess.check_output([str(path)], text=True, timeout=30)
            self.assertIn('private parser bytes OK', result)


@unittest.skipUnless(KDK and KC and KDK.exists() and KC.exists(), 'set explicit DARWIN27_IOSURFACE_DONOR and DARWIN27_BOOT_KC')
class Binding(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.donor, cls.collection = KDK.read_bytes(), KC.read_bytes()
        cls.layout = LAYOUT['capture'](cls.donor, cls.collection)

    def test_real_collection_entry(self):
        layout = self.layout
        self.assertEqual(layout['prefix_hex'], '554889e541574156415541545350')
        self.assertEqual(layout['symbol'], LAYOUT['SYMBOL'])
        self.assertEqual(len(LAYOUT['definitions'](layout, self.collection)), 4)

    def test_changed_collection_rejected(self):
        changed = bytearray(self.collection)
        changed[int(self.layout['entry_offset'], 0)] ^= 1
        with self.assertRaisesRegex(ValueError, 'another collection'):
            LAYOUT['definitions'](self.layout, changed)

    def test_unsafe_prefix_rejected(self):
        layout = dict(self.layout, prefix_hex='55')
        with self.assertRaisesRegex(ValueError, 'unsafe trampoline'):
            LAYOUT['definitions'](layout, self.collection)

    def test_incorrect_entry_rejected(self):
        layout = dict(self.layout, entry_address=hex(int(self.layout['entry_address'], 0)+1))
        with self.assertRaisesRegex(ValueError, 'collection member'):
            LAYOUT['definitions'](layout, self.collection)

    def test_incorrect_header_address_rejected(self):
        layout = dict(self.layout, header_address=hex(int(self.layout['header_address'], 0)+1))
        with self.assertRaisesRegex(ValueError, 'collection member'):
            LAYOUT['definitions'](layout, self.collection)

    def test_thin_and_fat_bind_identically(self):
        self.assertEqual(self.layout, LAYOUT['capture'](LAYOUT['thin'](self.donor), self.collection))

    def test_modified_donor_code_rejected(self):
        donor = bytearray(LAYOUT['thin'](self.donor))
        start = LAYOUT['MACHO']['symbols'](donor)[LAYOUT['SYMBOL']]
        donor[LAYOUT['file_offset'](bytes(donor), start)] ^= 1
        with self.assertRaisesRegex(ValueError, 'input ABI'):
            LAYOUT['capture'](bytes(donor), self.collection)

    def test_target_descriptor_virtual_slots(self):
        kernel = Path(os.environ['DARWIN27_TARGET_KERNEL']) if os.environ.get('DARWIN27_TARGET_KERNEL') else None
        if kernel is None or not kernel.exists():
            self.skipTest('target kernel unavailable')
        data = kernel.read_bytes()
        LAYOUT['validate_descriptor_abi'](data)
        table = LAYOUT['file_offset'](data, LAYOUT['MACHO']['symbols'](data)['__ZTV25IOGeneralMemoryDescriptor'])+16
        changed = bytearray(data)
        changed[table+0x1e0] ^= 1
        with self.assertRaisesRegex(ValueError, 'virtual slot'):
            LAYOUT['validate_descriptor_abi'](bytes(changed))
