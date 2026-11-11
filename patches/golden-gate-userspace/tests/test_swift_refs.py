import importlib.util
from pathlib import Path
import struct
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]/'src'
for name in ('legacy_trackpad', 'swift_refs'):
    spec = importlib.util.spec_from_file_location(name, ROOT/(name+'.py'))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
refs = sys.modules['swift_refs']
apply_edits = sys.modules['legacy_trackpad'].apply_edits

BASE = 0x7ff800000000
IMAGE = b'/System/Library/Frameworks/Fixture.framework/Versions/A/Fixture'
MODULE, PROTOCOL, STRUCT, TAG, DUPLICATE = (BASE+0x2000+0x10*i for i in range(5))
STRINGS, GARBAGE, RECORD, TYPES, PROTO = BASE+0x2100, BASE+0x2180, BASE+0x2200, BASE+0x2300, BASE+0x2400


def cache(path, arch, broken=False, duplicate=False):
    """One-file cache family: header, one mapping, one image with a nested enum conforming to a protocol."""
    data = bytearray(0x3000)
    data[:16] = f'dyld_v1 {arch:>7}'.encode().ljust(16, b'\0')
    struct.pack_into('<II', data, 0x138, 0x200, 1)
    struct.pack_into('<QQQQQQII', data, 0x200, BASE, len(data), 0, 0, 0, 0, 5, 5)
    struct.pack_into('<II', data, 0x1c0, 0x240, 1)
    struct.pack_into('<QQQI', data, 0x240, BASE+0x1000, 0, 0, 0x280)
    data[0x280:0x280+len(IMAGE)+1] = IMAGE+b'\0'

    def put(address, fmt, *values):
        struct.pack_into(fmt, data, address-BASE, *values)

    def rel(field, target):
        put(field, '<i', target-field)

    types = [STRUCT, TAG] + ([DUPLICATE] if duplicate else [])
    put(BASE+0x1000, '<8I', 0xfeedfacf, 0x1000007, 3, 6, 1, 72+2*80, 0, 0)
    put(BASE+0x1020, '<II16sQQQQIIII', 0x19, 72+2*80, b'__TEXT', BASE, len(data), 0, len(data), 5, 5, 2, 0)
    put(BASE+0x1068, '<16s16sQQ', b'__swift5_types', b'__TEXT', TYPES, 4*len(types))
    put(BASE+0x10b8, '<16s16sQQ', b'__swift5_proto', b'__TEXT', PROTO, 4)
    names = {}
    at = STRINGS
    for text in (b'Fixture', b'P', b'S', b'Tag'):
        data[at-BASE:at-BASE+len(text)+1] = text+b'\0'
        names[text], at = at, at+len(text)+1
    for descriptor, kind, parent, name in ((MODULE, 0, None, b'Fixture'), (PROTOCOL, 3, MODULE, b'P'),
                                           (STRUCT, 17, MODULE, b'S'), (TAG, 18, STRUCT, b'Tag'),
                                           (DUPLICATE, 18, STRUCT, b'Tag')):
        put(descriptor, '<I', kind)
        if parent:
            rel(descriptor+4, parent)
        rel(descriptor+8, names[name])
    put(GARBAGE, '<I', 0x14)
    rel(RECORD, PROTOCOL)
    rel(RECORD+4, GARBAGE if broken else TAG)
    for i, descriptor in enumerate(types):
        rel(TYPES+4*i, descriptor)
    rel(PROTO, RECORD)
    Path(path).write_bytes(data)


class SwiftRefsTest(unittest.TestCase):
    def family(self, root, **options):
        cache(root/'dyld_shared_cache_x86_64', 'x86_64', **options)
        cache(root/'dyld_shared_cache_arm64e', 'arm64e')
        return root/'dyld_shared_cache_x86_64'

    def test_verifier_reports_conformance_type_outside_any_descriptor(self):
        with tempfile.TemporaryDirectory() as d:
            self.family(Path(d))
            self.assertEqual(refs.verify(d)[2], [])
            self.family(Path(d), broken=True)
            images, totals, issues = refs.verify(d)
            self.assertEqual((images, totals['proto'], totals['types']), (1, 1, 2))
            self.assertEqual(len(issues), 1)
            self.assertIn('kind 20', issues[0])

    def test_repair_points_reference_at_the_arm64e_named_type_and_verifies_clean(self):
        with tempfile.TemporaryDirectory() as d:
            x86 = self.family(Path(d), broken=True)
            before = x86.read_bytes()
            plan = refs.plan_cache_family(d)
            self.assertEqual(list(plan), [x86])
            [(offset, original, replacement, reason)] = plan[x86]
            self.assertEqual(offset, RECORD+4-BASE)
            self.assertEqual(struct.unpack('<i', replacement)[0], TAG-(RECORD+4))
            self.assertIn('Fixture.S.Tag: Fixture.P', reason)
            apply_edits(x86, plan[x86])
            after = x86.read_bytes()
            self.assertEqual([i for i in range(len(before)) if before[i] != after[i]],
                             [i for i in range(offset, offset+4) if before[i] != after[i]])
            self.assertEqual(refs.verify(d)[2], [])
            self.assertEqual(refs.plan_cache_family(d), {})

    def test_repair_refuses_ambiguous_or_missing_reference(self):
        with tempfile.TemporaryDirectory() as d:
            self.family(Path(d), broken=True, duplicate=True)
            with self.assertRaisesRegex(ValueError, '2 x86_64 descriptors match'):
                refs.plan_cache_family(d)
            (Path(d)/'dyld_shared_cache_arm64e').unlink()
            with self.assertRaises(FileNotFoundError):
                refs.plan_cache_family(d)


if __name__ == '__main__':
    unittest.main()
