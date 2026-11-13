"""metal-adapter: abi.py's layout logic, the private-struct translation table it generates (layouts.inc) and, with a
built bundle, the shim's translation of real 27 descriptors under Rosetta.
python3.14 -I -B -m unittest discover -s tools/metal-adapter/tests
METAL_ADAPTER_METALOLD=PATH/MetalOld.dylib METAL_ADAPTER_METAL27=OUT/work/Metal27.cut \
METAL_ADAPTER_BUNDLE=OUT/metal27-adapter-test python3.14 -I -B -m unittest discover -s tools/metal-adapter/tests

Without Apple binaries: abi.py's parser, layout and field mapping on small encodings written here.
layouts.inc carries Apple's encodings, so it is never committed: with METAL_ADAPTER_METALOLD and
METAL_ADAPTER_METAL27 (the hash-checked inputs make.py uses) it is generated into a temporary folder, recomputed
from the two encodings it carries, and its bit positions are checked against what the getters of both binaries read
(read off their disassembly once, below). With METAL_ADAPTER_BUNDLE (needs Xcode and Rosetta): descriptors made through 27's public API are read through the
shim twice, as an ordinary caller (27's struct) and from a dylib in a folder named like the driver bundle (the
MetalOld-layout copy), and every value the driver reads is checked at MetalOld's offset."""
import json
import os
from pathlib import Path
import re
import runpy
import shutil
import subprocess
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
TOOL = HERE.parent
ABI = runpy.run_path(str(TOOL / 'abi.py'))
MAKE = runpy.run_path(str(TOOL / 'make.py'))
_INC = []


def inc():
    """layouts.inc generated from the env-provided MetalOld and Metal cut (checked against inputs.json)"""
    if not _INC:
        old, new = os.environ['METAL_ADAPTER_METALOLD'], os.environ['METAL_ADAPTER_METAL27']
        if MAKE['sha'](old) != MAKE['INPUTS']['metalold']['sha256']:
            raise AssertionError(f'{old} is not the MetalOld in inputs.json')
        if MAKE['sha'](new) != MAKE['INPUTS']['metal27']['cut_sha256']:
            raise AssertionError(f'{new} is not the Metal cut in inputs.json')
        with tempfile.TemporaryDirectory() as t:
            out = Path(t, 'layouts.inc')
            subprocess.run(['python3.14', '-I', str(TOOL / 'abi.py'), 'layouts', old, new, '--out', str(out)],
                           check=True, capture_output=True)
            _INC.append(out.read_text())
    return _INC[0]


def c_unquote(s):
    return s[1:-1].replace('\\"', '"').replace('\\\\', '\\')


def table(text=None):
    """{struct: {'class', 'sel', 'osize', 'nsize', 'nenc', 'oenc', 'fields': {name: (oo, os, ob, ow, no, ns, nb, nw, cv)}}}"""
    out = {}
    for m in re.finditer(r'LAYOUT_BEGIN\((\w+), (\w+), (\w+), (\d+), (\d+), ("(?:[^"\\]|\\.)*"),\s*("(?:[^"\\]|\\.)*")\)(.*?)LAYOUT_END',
                         text if text is not None else inc(), re.S):
        fields = {}
        for f in re.finditer(r'FIELD\(("(?:[^"\\]|\\.)*"), (-?\d+), (-?\d+), (-?\d+), (-?\d+), (-?\d+), (-?\d+), (-?\d+), (-?\d+), (-?\d+)\)', m.group(8)):
            fields[c_unquote(f.group(1))] = tuple(int(f.group(k)) for k in range(2, 11))
        out[m.group(3)] = {'class': m.group(1), 'sel': m.group(2), 'osize': int(m.group(4)), 'nsize': int(m.group(5)),
                           'nenc': c_unquote(m.group(6)), 'oenc': c_unquote(m.group(7)), 'fields': fields}
    return out


# What each binary's getter reads, from its disassembly (MetalOld: OCLP 13.2.1-25, 27: 26B131 Metal 383.1), as
# (struct offset, bit, bits) of the private struct; byte fields as (offset, None, None).
GETTERS_OLD = {
    'MTLRenderPipelineDescriptorPrivate': {'alphaToCoverageEnabled': (208, 0, 1), 'rasterizationEnabled': (208, 2, 1),
                                           'rasterSampleCount': (168, None, None), 'vertexDescriptor': (256, None, None)},
    'MTLRenderPipelineAttachmentDescriptorPrivate': {'pixelFormat': (0, 36, 28), 'writeMask': (0, 27, 4),
                                                     'destinationAlphaBlendFactor': (0, 22, 5)},
}
GETTERS_27 = {
    'MTLRenderPipelineDescriptorPrivate': {'alphaToCoverageEnabled': (224, 0, 2), 'alphaToOneEnabled': (224, 2, 2),
                                           'rasterizationEnabled': (224, 4, 1), 'alphaTestFunc': (224, 25, 3),
                                           'logicOp': (224, 32, 4), 'logicOpEnabled': (224, 36, 1),
                                           'forceSoftwareVertexFetch': (224, 38, 1),
                                           'meshThreadgroupSizeIsMultipleOfThreadExecutionWidth': (224, 40, 1)},
    'MTLRenderPipelineAttachmentDescriptorPrivate': {'blendingEnabled': (0, 0, 2), 'writeMask': (0, 32, 5),
                                                     'destinationAlphaBlendFactor': (0, 23, 5), 'pixelFormat': (0, 42, 22)},
}
# where the driver reads (11.7.10 MTLIGRenderPipelineState generate3DStateWM: byte 0xd0 bit 2; generate3DStateVF and
# vertexFunctionKey...: 0x100 = the vertex descriptor; 3DStateTE: 0x58 = tessellationPartitionMode)
DRIVER_READS = {'MTLRenderPipelineDescriptorPrivate': {'rasterizationEnabled': (0xd0, 2, 1),
                                                       'vertexDescriptor': (0x100, None, None),
                                                       'tessellationPartitionMode': (0x58, None, None)}}


HAVE_INPUTS = bool(os.environ.get('METAL_ADAPTER_METALOLD') and os.environ.get('METAL_ADAPTER_METAL27'))


class Synthetic(unittest.TestCase):
    """abi.py on encodings written here (the same shapes as Metal's: objects, unions, 32-bit bitfield runs)."""
    OLD = ('{S="obj"@"NSObject""count"Q"flag"c""(?="a"Q"b"Q)""(?="hash"[2I]""{?="on"b1"mode"b3"wide"b30"tail"b2})'
           '"ptr"^v"fn"^?"name"@"<Proto>""small"s"last"Q}')
    NEW = ('{S="obj"@"NSObject""count"Q"flag"c"inserted"q""(?="a"Q"b"Q)""(?="hash"[2I]""{?="on"b2"mode"b3"wide"b30"tail"b2})'
           '"ptr"^v"fn"^?"name"@"<Proto>""small"s"last"Q"added"Q}')

    def leaves(self, enc):
        name, size, leaves = ABI['struct_layout'](enc)
        return name, size, {p: (o, s, b, w) for p, o, s, b, w in leaves}

    def test_layout(self):
        name, size, f = self.leaves(self.OLD)
        self.assertEqual(name, 'S')
        self.assertEqual(f['obj'], (0, 8, 0, 0))
        self.assertEqual(f['flag'], (16, 1, 0, 0))
        self.assertEqual(f['a'], (24, 8, 0, 0))
        self.assertEqual(f['b'], (24, 8, 0, 0))          # union members share their offset
        self.assertEqual(f['hash'], (32, 8, 0, 0))
        self.assertEqual(f['on'], (32, 0, 0, 1))
        self.assertEqual(f['mode'], (32, 0, 1, 3))
        self.assertEqual(f['wide'], (32, 0, 32, 30))     # 4 + 30 would cross bit 32: starts the next 32-bit unit
        self.assertEqual(f['tail'], (32, 0, 62, 2))
        self.assertEqual(f['ptr'], (40, 8, 0, 0))
        self.assertEqual(f['fn'], (48, 8, 0, 0))
        self.assertEqual(f['name'], (56, 8, 0, 0))       # @"<Proto>" is a type, not the next field's name
        self.assertEqual(f['small'], (64, 2, 0, 0))
        self.assertEqual(f['last'], (72, 8, 0, 0))
        self.assertEqual(size, 80)

    def test_fieldmap(self):
        sname, osize, nsize, fields, moved = ABI['fieldmap'](self.OLD, self.NEW)
        self.assertTrue(moved)
        self.assertEqual((sname, osize, nsize), ('S', 80, 96))
        f = {p: (o, s, b, w, no, ns, nb, nw) for p, o, s, b, w, no, ns, nb, nw in fields}
        self.assertEqual(f['count'], (8, 8, 0, 0, 8, 8, 0, 0))
        self.assertEqual(f['a'], (24, 8, 0, 0, 32, 8, 0, 0))   # moved by the inserted field
        self.assertEqual(f['on'], (32, 0, 0, 1, 40, 0, 0, 2))  # widened
        self.assertEqual(f['mode'], (32, 0, 1, 3, 40, 0, 2, 3))
        self.assertNotIn('hash', f)                             # union alias over bitfields is not copied
        self.assertNotIn('added', f)                            # 27-only fields have no MetalOld place
        self.assertEqual(f['last'][4], 80)
        self.assertEqual(f['wide'][4:], (40, 0, 32, 30))

    def test_absent_and_unchanged(self):
        new = self.OLD.replace('"small"s', '')
        _, _, _, fields, moved = ABI['fieldmap'](self.OLD, new)
        self.assertTrue(moved)
        f = {x[0]: x for x in fields}
        self.assertIsNone(f['small'][5])
        _, _, _, _, moved = ABI['fieldmap'](self.OLD, self.OLD + '')
        self.assertFalse(moved)

    def test_size_change_is_refused(self):
        with self.assertRaises(ValueError):
            ABI['fieldmap']('{T="x"I"y"Q}', '{T="x"Q"y"Q}')

    def test_write_inc_round_trip(self):
        sname, osize, nsize, fields, _ = ABI['fieldmap'](self.OLD, self.NEW)
        with tempfile.TemporaryDirectory() as t:
            out = Path(t, 'l.inc')
            ABI['write_inc']([('SInternal', '_descriptorPrivate', sname, osize, nsize, self.NEW, fields, self.OLD)], out)
            got = table(out.read_text())
        self.assertEqual(got['S']['oenc'], self.OLD)
        self.assertEqual(got['S']['nenc'], self.NEW)
        self.assertEqual(got['S']['fields']['mode'][:8], (32, 0, 1, 3, 40, 0, 2, 3))


@unittest.skipUnless(HAVE_INPUTS, 'set METAL_ADAPTER_METALOLD and METAL_ADAPTER_METAL27 (layouts.inc is generated)')
class Table(unittest.TestCase):
    def test_three_structs(self):
        self.assertEqual(sorted(table()), ['MTLRenderPipelineAttachmentDescriptorPrivate',
                                           'MTLRenderPipelineDescriptorPrivate', 'MTLTextureDescriptorPrivate'])

    def test_table_is_what_abi_computes(self):
        for name, t in table().items():
            sname, osize, nsize, fields, _ = ABI['fieldmap'](t['oenc'], t['nenc'])
            self.assertEqual((sname, osize, nsize), (name, t['osize'], t['nsize']))
            got = {p: (o, s, b, w, -1 if no is None else no, ns, nb, nw) for p, o, s, b, w, no, ns, nb, nw in fields}
            self.assertEqual(got, {p: v[:8] for p, v in t['fields'].items()})

    def test_bit_positions_match_the_getters(self):
        t = table()
        for which, getters, col in (('MetalOld', GETTERS_OLD, 0), ('27', GETTERS_27, 4)):
            for sname, fields in getters.items():
                for f, (off, bit, bits) in fields.items():
                    v = t[sname]['fields'][f]
                    o, s, b, w = v[col:col + 4]
                    with self.subTest(which=which, field=f):
                        self.assertEqual(o, off)
                        if bit is not None:
                            self.assertEqual((s, b, w), (0, bit, bits))

    def test_driver_reads_land_on_the_right_fields(self):
        t = table()
        for sname, fields in DRIVER_READS.items():
            for f, (off, bit, bits) in fields.items():
                o, s, b, w = t[sname]['fields'][f][:4]
                self.assertEqual(o + (b // 8 if bit is not None else 0), off + (bit // 8 if bit is not None else 0))
                if bit is not None:
                    self.assertEqual((b % 8, w), (bit, bits))

    def test_union_aliases_are_not_copied_over_bitfields(self):
        t = table()
        self.assertNotIn('miscHash', t['MTLRenderPipelineDescriptorPrivate']['fields'])
        self.assertNotIn('bits', t['MTLRenderPipelineAttachmentDescriptorPrivate']['fields'])

    def test_widened_flags_keep_their_meaning(self):
        f = table()['MTLRenderPipelineAttachmentDescriptorPrivate']['fields']
        self.assertEqual(f['blendingEnabled'][8], 2)        # 27's isBlendingEnabled: (bits & 3) == 1
        f = table()['MTLRenderPipelineDescriptorPrivate']['fields']
        self.assertEqual(f['alphaToCoverageEnabled'][8], 1)  # 27's isAlphaToCoverageEnabled: nonzero


def bits(hexs, off, bit, w):
    b = bytes.fromhex(hexs)
    v = int.from_bytes(b[off:off + 8].ljust(8, b'\0'), 'little')
    return (v >> bit) & ((1 << w) - 1)


def u64(hexs, off):
    return int.from_bytes(bytes.fromhex(hexs)[off:off + 8], 'little')


@unittest.skipUnless(os.environ.get('METAL_ADAPTER_BUNDLE'), 'set METAL_ADAPTER_BUNDLE to a built bundle')
class Probe(unittest.TestCase):
    """27's own descriptors, read through the shim from a stand-in driver image."""
    @classmethod
    def setUpClass(cls):
        b = Path(os.environ['METAL_ADAPTER_BUNDLE']).resolve()
        cls.tmp = tempfile.mkdtemp()
        d = Path(cls.tmp, 'HaoProbeGraphicsMTLDriver.bundle')
        d.mkdir()
        cc = ['xcrun', 'clang', '-arch', 'x86_64', '-mmacosx-version-min=27.0', '-O1', '-fobjc-arc', '-Wno-deprecated-declarations']
        src = str(HERE / 'abi_probe.m')
        subprocess.run(cc + ['-DPROBE_DYLIB', '-dynamiclib', src, '-o', str(d / 'probe.dylib')], check=True)
        subprocess.run(cc + [src, '-framework', 'Foundation', '-framework', 'Metal', '-o', str(Path(cls.tmp, 'abi_probe'))],
                       check=True)
        for f in (d / 'probe.dylib', Path(cls.tmp, 'abi_probe')):
            subprocess.run(['codesign', '-f', '-s', '-', str(f)], check=True, capture_output=True)
        r = subprocess.run(['/usr/bin/env', f'DYLD_FRAMEWORK_PATH={b}/fw', str(Path(cls.tmp, 'abi_probe')), str(d / 'probe.dylib')],
                           capture_output=True, text=True, timeout=120)
        cls.log = r.stderr
        cls.out = json.loads(r.stdout) if r.returncode == 0 else None

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        self.assertIsNotNone(self.out, self.log)
        self.assertIn('[shim] layouts: -[MTLTextureDescriptorInternal descriptorPrivate]', self.log)

    def test_texture(self):
        d, o = self.out['texture']['direct'], self.out['texture']['driver']
        self.assertEqual(len(bytes.fromhex(d)), 216)
        for name, off in (('pixelFormat', 8), ('width', 16), ('height', 24)):
            self.assertEqual(u64(o, off), u64(d, off), name)
        self.assertEqual((u64(o, 8), u64(o, 16), u64(o, 24)), (70, 64, 32))
        self.assertEqual(u64(o, 104), 5)                                    # usage: RenderTarget | ShaderRead
        self.assertEqual((u64(o, 112) >> 4) & 0xf, 1)                       # storageMode (resourceOptions): Managed
        for name, oo, no in (('resolvedUsage', 168, 192), ('cpuCacheMode', 176, 200), ('storageMode', 184, 208)):
            self.assertEqual(u64(o, oo), u64(d, no), name)                  # the three fields 27 moved

    def test_pipeline(self):
        d, o = self.out['pipeline']['direct'], self.out['pipeline']['driver']
        self.assertEqual(bits(o, 0xd0, 2, 1), 0)                            # rasterizationEnabled = NO, as the driver reads it
        self.assertEqual(bits(o, 0xd0, 0, 1), 1)                            # alphaToCoverageEnabled
        self.assertEqual(u64(o, 168), 4)                                    # rasterSampleCount
        self.assertEqual(u64(o, 72), 252)                                   # depthAttachmentPixelFormat Depth32Float
        self.assertEqual(u64(o, 0x100), u64(d, 272))                        # vertexDescriptor: 27's object, at 0x100
        self.assertNotEqual(u64(o, 0x100), 0)
        self.assertEqual(u64(o, 232), int(self.out['pipeline']['label'], 16))
        self.assertEqual(bits(d, 224, 4, 1), 0)                             # 27's own struct is untouched
        self.assertEqual(u64(d, 0), u64(o, 0))                              # attachments array

    def test_colour_attachment(self):
        o = self.out['attachment']['driver']
        self.assertEqual(bits(o, 0, 36, 28), 80)                            # pixelFormat BGRA8Unorm
        self.assertEqual(bits(o, 0, 0, 1), 1)                               # blendingEnabled
        self.assertEqual(bits(o, 0, 27, 4), 0x9)                            # writeMask Red | Alpha
        self.assertEqual(bits(o, 0, 22, 5), 5)                              # destinationAlphaBlendFactor OneMinusSourceAlpha
        self.assertEqual(bits(o, 0, 1, 3), 1)                               # rgbBlendOperation Subtract
        self.assertEqual(bits(o, 0, 7, 5), 4)                               # sourceRGBBlendFactor SourceAlpha
        d = self.out['attachment']['direct']
        self.assertEqual(bits(d, 0, 42, 22), 80)                            # 27's own: pixelFormat at bit 42


if __name__ == '__main__':
    unittest.main()
