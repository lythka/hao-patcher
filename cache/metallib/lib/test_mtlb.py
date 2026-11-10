import hashlib
import json
from pathlib import Path
import struct
import unittest
import mtlb


def tag(name, blob):
    return name + struct.pack('<H', len(blob)) + blob


def fixture(trailer=b'ILST', shared=True):
    blob = b'local synthetic bitcode'
    entries = []
    for name in ([b'a', b'b'] if shared else [b'a']):
        body = tag(b'NAME', name + b'\0') + tag(b'OFFT', struct.pack('<3Q', 0, 0, 0))
        body += tag(b'MDSZ', struct.pack('<Q', len(blob))) + tag(b'HASH', hashlib.sha256(blob).digest()) + b'ENDT'
        entries.append(struct.pack('<I', len(body) + 4) + body)
    functions = b''.join(entries)
    global_size = 22 + 4
    pmo = 88 + 4 + len(functions) + global_size
    bco = pmo
    globals = tag(trailer, struct.pack('<2Q', bco + len(blob), 4)) + b'ENDT'
    header = bytearray(88); header[:4] = b'MTLB'
    struct.pack_into('<HH', header, 6, 2, 9)
    struct.pack_into('<9Q', header, 16, bco + len(blob) + 4, 88, len(functions), pmo, 0, pmo, 0, bco, len(blob))
    return bytes(header) + struct.pack('<I', len(entries)) + functions + globals + blob + b'tail'


class ContainerTests(unittest.TestCase):
    def test_all_trailer_kinds_relocated_and_aliases_updated(self):
        for trailer in (b'HDYN', b'RLST', b'ILST'):
            data = fixture(trailer)
            self.assertEqual(mtlb.splice(data, {}), data)
            new = mtlb.splice(data, {'a': b'longer synthetic bitcode to resize the section'})
            lib = mtlb.check(new)
            self.assertEqual(lib['fns'][0]['hash'], lib['fns'][1]['hash'])
            self.assertEqual(mtlb.bitcode(new, lib, lib['fns'][1]), b'longer synthetic bitcode to resize the section')
            t = lib['trailers'][0]
            self.assertEqual(new[t['offset']:t['offset'] + t['size']], b'tail')

    def test_corrupt_hash_metadata_size_version_and_tail_refuse(self):
        data = fixture(); lib = mtlb.check(data)
        for kind in ('hash', 'unknown', 'version', 'width', 'gap', 'overlap', 'tail', 'size', 'offset'):
            broken = bytearray(data)
            pos = lib['trailers'][0]['position']
            if kind == 'hash': broken[lib['hdr'][7]] ^= 1
            elif kind == 'unknown': broken[pos - 6:pos - 2] = b'WHAT'
            elif kind == 'version': struct.pack_into('<H', broken, 8, 10)
            elif kind == 'width': struct.pack_into('<H', broken, pos - 2, 15)
            elif kind == 'gap': struct.pack_into('<Q', broken, pos, lib['trailers'][0]['offset'] + 1)
            elif kind == 'overlap': struct.pack_into('<Q', broken, pos, lib['trailers'][0]['offset'] - 1)
            elif kind == 'tail':
                broken += b'?'; struct.pack_into('<Q', broken, 16, len(broken))
            elif kind == 'size': struct.pack_into('<Q', broken, 16, len(broken) - 1)
            else: struct.pack_into('<Q', broken, lib['fns'][0]['tags'][b'OFFT'][0] + 16, 9999)
            with self.subTest(kind=kind), self.assertRaises(ValueError): mtlb.check(bytes(broken))

    def test_conflicting_shared_and_unknown_replacements_refuse(self):
        for replacements in ({'a': b'x', 'b': b'y'}, {'unknown': b'x'}):
            with self.assertRaises(ValueError): mtlb.splice(fixture(), replacements)


    def test_factory_hash_exception_does_not_cover_modified_files(self):
        # A synthetic mismatch is never approved by an unrelated factory pin.
        data = bytearray(fixture()); lib = mtlb.check(bytes(data))
        data[lib['fns'][0]['tags'][b'HASH'][0]] ^= 1
        with self.assertRaisesRegex(ValueError, 'hash differs'): mtlb.check(bytes(data))
