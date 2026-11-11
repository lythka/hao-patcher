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


CONTEXT, POINTERS, STRINGS = 0x2ee43, 0x30000, 0x30100


def image_fixture(base, size):
    """Native MPSNDArray image whose context references resolve inside it."""
    image = bytearray(size)
    struct.pack_into('<8I', image, 0, 0xfeedfacf, 0x1000007, 3, 6, 2, 176, 0, 0)
    struct.pack_into('<II16sQQQQIIII', image, 32, 0x19, 152, b'__TEXT',
                     base, size, 0, size, 5, 5, 1, 0)
    struct.pack_into('<16s16sQQ', image, 104, b'__text', b'__TEXT', base + 0x1000, 0x2f000)
    struct.pack_into('<II16s', image, 184, 0x1b, 24, bytes(range(16)))
    context = bytearray(mps.ORIGINAL)
    for field, slot in (('selector_reference', POINTERS), ('class_reference', POINTERS + 8)):
        start = mps.FIELDS[field]
        struct.pack_into('<i', context, start, slot - (CONTEXT + start + 4))
    image[CONTEXT:CONTEXT + len(context)] = context
    struct.pack_into('<QQ', image, POINTERS, base + STRINGS, base + POINTERS + 16)
    struct.pack_into('<Q', image, POINTERS + 48, base + POINTERS + 64)
    struct.pack_into('<Q', image, POINTERS + 88, base + STRINGS + 0x100)
    image[STRINGS:STRINGS + len(mps.SELECTOR)] = mps.SELECTOR
    image[STRINGS + 0x100:STRINGS + 0x100 + len(mps.CLASS)] = mps.CLASS
    return image, bytes(context)


def cache_fixture():
    limit, signature_size = 0x32000, 0x4000
    data = bytearray(limit + signature_size)
    data[:16] = b'dyld_v1  x86_64\x00'
    struct.pack_into('<II', data, 16, 0x200, 1)
    struct.pack_into('<QQ', data, 0x28, limit, signature_size)
    base = 0x7FF814090000
    struct.pack_into('<QQQII', data, 0x200, base - 0x1000, limit, 0, 5, 5)
    struct.pack_into('<II', data, 448, 0x500, 1)
    struct.pack_into('<QQQII', data, 0x500, base, 0, 0, 0x600, 0)
    data[0x600:0x600 + len(mps.IMAGE) + 1] = mps.IMAGE + b'\0'
    image, context = image_fixture(base, limit - 0x1000)
    data[0x1000:limit] = image
    offset = 0x1000 + CONTEXT
    struct.pack_into('>5I', data, limit, 0xfade0cc0, signature_size, 1, 0, 20)
    struct.pack_into('>9I4B', data, limit + 20, 0xfade0c02, 100, 0x20400,
                     2, 96, 88, 2, limit // 4096, limit, 32, 2, 0, 14)
    data[limit + 108:limit + 116] = b'fixture\0'
    return data, offset, context


class ApplyTests(unittest.TestCase):
    def test_full_application_resigns_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch)
            source = root / 'source'
            source.mkdir()
            data, offset, context = cache_fixture()
            original = source / 'dyld_shared_cache_x86_64'
            original.write_bytes(data)
            report = module.apply(source, root / 'first')
            target = root / 'first' / original.name
            result = target.read_bytes()
            self.assertEqual(original.read_bytes(), data)
            self.assertEqual(result[offset:offset + len(context)],
                             mps.patch_initializer(context).data)
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
