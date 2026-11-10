"""Real cache traversal, exact native code edits, signatures and idempotence."""
import hashlib
import importlib.util
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parents[1]  # repository root: patches/, cache/, tools/
spec = importlib.util.spec_from_file_location('mps_apply', ROOT / 'mps_apply.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
sys.path.insert(0, str(REPO / 'patches/golden-gate-userspace/src'))
sys.path.insert(0, str(ROOT / 'lib'))  # dyld_cache, mps_matrix_initializer
import mps_matrix_initializer as mps


def cache_fixture():
    limit, signature_size = 0x32000, 0x4000
    data = bytearray(limit + signature_size)
    data[:16] = b'dyld_v1  x86_64\x00'
    struct.pack_into('<II', data, 16, 0x200, 1)
    struct.pack_into('<QQ', data, 0x28, limit, signature_size)
    base = mps.PREFERRED_ADDRESS - mps.CONTEXT_OFFSET
    struct.pack_into('<QQQII', data, 0x200, base - 0x1000, limit, 0, 5, 5)
    struct.pack_into('<II', data, 448, 0x500, 1)
    struct.pack_into('<QQQII', data, 0x500, base, 0, 0, 0x600, 0)
    data[0x600:0x600 + len(mps.IMAGE) + 1] = mps.IMAGE + b'\0'
    struct.pack_into('<8I', data, 0x1000, 0xfeedfacf, 0x1000007, 3, 6, 2, 96, 0, 0)
    struct.pack_into('<II', data, 0x1020, 0x1b, 24)
    data[0x1028:0x1038] = mps.UUID
    struct.pack_into('<II16sQQ', data, 0x1038, 0x19, 72, b'__TEXT', base, limit - 0x1000)
    offset = 0x1000 + mps.CONTEXT_OFFSET
    data[offset:offset + len(mps.ORIGINAL)] = mps.ORIGINAL
    struct.pack_into('>5I', data, limit, 0xfade0cc0, signature_size, 1, 0, 20)
    struct.pack_into('>9I4B', data, limit + 20, 0xfade0c02, 100, 0x20400,
                     2, 96, 88, 2, limit // 4096, limit, 32, 2, 0, 14)
    data[limit + 108:limit + 116] = b'fixture\0'
    return data, offset


class ApplyTests(unittest.TestCase):
    def test_full_application_resigns_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch)
            source = root / 'source'
            source.mkdir()
            data, offset = cache_fixture()
            original = source / 'dyld_shared_cache_x86_64'
            original.write_bytes(data)
            report = module.apply(source, root / 'first')
            target = root / 'first' / original.name
            result = target.read_bytes()
            self.assertEqual(original.read_bytes(), data)
            self.assertEqual(result[offset:offset + len(mps.FIXED)], mps.FIXED)
            self.assertEqual(report['actions'][0]['edits'], 1)
            signature, _ = struct.unpack_from('<QQ', result, 0x28)
            self.assertEqual(result[signature + 36 + 39], 12)
            hashes = struct.unpack_from('>I', result, signature + 36 + 16)[0]
            slots = struct.unpack_from('>I', result, signature + 36 + 28)[0]
            for index in range(slots):
                start = signature + 36 + hashes + index * 32
                self.assertEqual(result[start:start + 32], hashlib.sha256(
                    result[index * 4096:(index + 1) * 4096]).digest())
            second = module.apply(root / 'first', root / 'second')
            self.assertEqual(second['actions'][0]['edits'], 0)
            self.assertEqual(second['outputs'], report['outputs'])
            with self.assertRaises(FileExistsError):
                module.apply(source, root / 'first')


if __name__ == '__main__':
    unittest.main()
