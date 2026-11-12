"""Host tests for make_gap.py's parsers and table assembly, on small text fixtures and synthetic Mach-O bytes.

Run: python3.14 -I -B -m unittest discover -s tools/metal-wrapper/spec/tests
"""
import importlib.util
import json
import os
import struct
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
FIX = HERE / 'fixtures'
_spec = importlib.util.spec_from_file_location('make_gap', HERE.parent / 'make_gap.py')
mg = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mg)


def fixture(name):
    return (FIX / name).read_text()


def dylib_cmd(cmd, path):
    name = path.encode() + b'\0'
    size = (24 + len(name) + 7) & ~7
    return struct.pack('<IIIIII', cmd, size, 24, 2, 0x10000, 0x10000) + name.ljust(size - 24, b'\0')


def thin_x86_64(paths, cmd=0x0C):
    cmds = b''.join(dylib_cmd(cmd, p) for p in paths)
    cmds += struct.pack('<II', 0x1B, 24) + b'\0' * 16      # LC_UUID, ignored
    hdr = struct.pack('<IiiIIIII', mg.MH_MAGIC_64, mg.CPU_TYPE_X86_64, 3, 6, len(paths) + 1, len(cmds), 0, 0)
    return hdr + cmds


def thin_arm64(paths):
    cmds = b''.join(dylib_cmd(0x0C, p) for p in paths)
    return struct.pack('<IiiIIIII', mg.MH_MAGIC_64, 0x0100000C, 2, 6, len(paths), len(cmds), 0, 0) + cmds


def fat(slices):
    """slices: [(cputype, bytes)] -> universal binary with 4 KiB aligned slices."""
    out = struct.pack('>II', mg.FAT_MAGIC, len(slices))
    offset, body = 4096, b''
    table = b''
    for cputype, data in slices:
        table += struct.pack('>iiIII', cputype, 3, offset + len(body), len(data), 12)
        body += data.ljust((len(data) + 4095) & ~4095, b'\0')
    return (out + table).ljust(4096, b'\0') + body


def build_trie(tree):
    """Serialize (edge, terminal, children) nodes as a Mach-O export trie; offsets stay below 128 (1-byte ULEB)."""
    nodes = []

    def collect(node):
        nodes.append(node)
        for child in node[2]:
            collect(child)
    collect(tree)
    terminal = b'\x02\x00\x10'                            # size 2: flags 0, offset 0x10

    def size(node):
        return len(terminal if node[1] else b'\x00') + 1 + sum(len(c[0]) + 2 for c in node[2])
    offsets, pos = {}, 0
    for node in nodes:
        offsets[id(node)] = pos
        pos += size(node)
    out = b''
    for node in nodes:
        out += (terminal if node[1] else b'\x00') + bytes([len(node[2])])
        for child in node[2]:
            out += child[0].encode() + b'\0' + bytes([offsets[id(child)]])
    return out


def reader(data):
    return lambda off, size: data[off:off + size]


class Parsers(unittest.TestCase):
    def test_ipsw_imports(self):
        cache, fs = mg.parse_ipsw_imports(fixture('ipsw-imports.txt'))
        self.assertEqual(cache, ['/System/Library/Frameworks/QuartzCore.framework/Versions/A/QuartzCore',
                                 '/System/Library/Frameworks/CoreVideo.framework/Versions/A/CoreVideo'])
        self.assertEqual(fs, ['/System/Applications/Books.app/Contents/Frameworks/BlissReader.framework/Versions/A/BlissReader',
                              '/System/Applications/Chess.app/Contents/MacOS/Chess',
                              '/usr/bin/mpsgraphtool'])

    def test_ipsw_symtab(self):
        s = mg.parse_ipsw_symtab(fixture('ipsw-symtab.txt'))
        self.assertEqual(s['exported'], {
            '_CVMTLTextureCreate': '__TEXT,__text',
            '_OBJC_CLASS_$_CVOwnClass': '__DATA,__objc_data',
            '_kCVSomeKey': '__DATA_CONST,__const',
            '__ZTS7Example': '__TEXT,__const',
            '_OBJC_IVAR_$_CVOwnClass._x': '__DATA,__objc_ivar',
        })
        self.assertIn('_canRunMetal', s['defined'])
        self.assertIn('-[CVFoo setBar:baz:]', s['defined'])
        metal = [(n, w) for n, lib, w in s['undefined'] if lib == 'Metal']
        self.assertEqual(metal, [
            ('_MTLCopyAllDevices', False),
            ('_OBJC_CLASS_$_MTLTextureDescriptor', False),
            ('_OBJC_CLASS_$_MTLResidencySetDescriptor', True),
            ('_OBJC_METACLASS_$_MTLRenderPassDescriptor', False),
            ('_MTLDeviceWasRemovedNotification', False),
            ('_OBJC_IVAR_$__MTLCommandBuffer._queue', False),
        ])
        self.assertIn(('_OBJC_CLASS_$_MPSKernel', 'MetalPerformanceShaders', False), s['undefined'])
        self.assertIn(('_CFArrayCreate', 'CoreFoundation', False), s['undefined'])

    def test_ipsw_objc(self):
        o = mg.parse_ipsw_objc(fixture('ipsw-objc.txt'))
        self.assertEqual(o['classes'], {'CVRootThing': None, 'CVPassDescriptor': 'MTLRenderPassDescriptor',
                                        'CVOwnClass': 'NSObject'})
        self.assertEqual(o['categories'], [('MTLTextureDescriptor', 'CVAdditions')])
        self.assertEqual(o['classrefs'], ['MTLSharedEventListener', 'NSArray'])

    def test_dyld_info(self):
        imports = mg.parse_dyld_info_imports(fixture('dyld_info-imports.txt'))
        self.assertEqual([i for i in imports if i[1] == 'Metal'], [
            ('_MTLCreateSystemDefaultDevice', 'Metal', False),
            ('_OBJC_CLASS_$_MTLComputePipelineDescriptor', 'Metal', False),
            ('_OBJC_CLASS_$_MTL4CompilerDescriptor', 'Metal', True),
        ])
        self.assertIn(('__linkguard_init', 'liblinkguard', True), imports)
        self.assertEqual(mg.parse_dyld_info_exports(fixture('dyld_info-exports.txt')), {
            '_MTL4CommandQueueErrorDomain', '_OBJC_CLASS_$_MTL4AccelerationStructureDescriptor', '_MTLResolver'})
        self.assertEqual(mg.parse_dyld_info_linked(fixture('dyld_info-linked.txt')), [
            ('re-export', '/System/Library/Frameworks/Metal.framework/Versions/A/MetalOld.dylib'),
            ('', '/usr/lib/libSystem.B.dylib'),
            ('', '/usr/lib/libobjc.A.dylib'),
        ])


    def test_ipsw_patches(self):
        self.assertEqual(mg.parse_ipsw_patches(fixture('ipsw-patches.txt')), {
            '__mtlValidateMTLTextureSwizzleKey': {'GOT'},
            '_OBJC_CLASS_$__MTL4ArgumentTable': {'AGXMetal13_3', 'AGXMetalG13X'},
            '_OBJC_CLASS_$_MTLSelfOnly': set(),
            '_OBJC_IVAR_$__MTLCommandBuffer._queue': {'IOGPU'},
        })

    def test_normalize(self):
        self.assertEqual(mg.normalize('OBJC_IVAR_$_C.x'), '_OBJC_IVAR_$_C.x')
        self.assertEqual(mg.normalize('_OBJC_IVAR_$_C.x'), '_OBJC_IVAR_$_C.x')
        self.assertEqual(mg.normalize('_MTLFoo'), '_MTLFoo')

    def test_exports_trie_command(self):
        self.assertEqual(mg.parse_ipsw_exports_trie_command(fixture('ipsw-loads.txt')), (0x2d28d348, 0xb638))
        self.assertIsNone(mg.parse_ipsw_exports_trie_command('005: LC_ID_DYLIB x'))

    def test_export_trie(self):
        # _MTL -> {A (terminal), B (terminal) -> {x (terminal)}}
        tree = ('', False, [('_MTL', False, [('A', True, []), ('B', True, [('x', True, [])])])])
        self.assertEqual(mg.parse_export_trie(build_trie(tree)), ['_MTLA', '_MTLB', '_MTLBx'])
        with self.assertRaises(ValueError):
            mg.parse_export_trie(b'\x00\x01a\x00\x00')     # the child edge points back at the root

    def test_gpu_driver_bundle(self):
        self.assertTrue(mg.is_gpu_driver_bundle('/System/Library/Extensions/AGXMetalG13X.bundle/Contents/MacOS/AGXMetalG13X'))
        self.assertTrue(mg.is_gpu_driver_bundle('[rosetta]/System/Library/Extensions/X.bundle/Contents/MacOS/X'))
        self.assertFalse(mg.is_gpu_driver_bundle('/System/Library/PrivateFrameworks/IOGPU.framework/Versions/A/IOGPU'))


class MachO(unittest.TestCase):
    def test_thin(self):
        data = thin_x86_64([mg.METAL_PATH, '/usr/lib/libSystem.B.dylib'])
        self.assertEqual(mg.macho_x86_64_dylibs(reader(data)),
                         [('load', mg.METAL_PATH), ('load', '/usr/lib/libSystem.B.dylib')])

    def test_weak_load(self):
        data = thin_x86_64([mg.METAL_PATH], cmd=0x80000018)
        self.assertEqual(mg.macho_x86_64_dylibs(reader(data)), [('weak', mg.METAL_PATH)])

    def test_fat_picks_x86_64_slice(self):
        data = fat([(0x0100000C, thin_arm64(['/arm/only'])), (mg.CPU_TYPE_X86_64, thin_x86_64([mg.METAL_PATH]))])
        self.assertEqual(mg.macho_x86_64_dylibs(reader(data)), [('load', mg.METAL_PATH)])

    def test_no_x86_64(self):
        self.assertIsNone(mg.macho_x86_64_dylibs(reader(thin_arm64([mg.METAL_PATH]))))
        self.assertIsNone(mg.macho_x86_64_dylibs(reader(fat([(0x0100000C, thin_arm64([mg.METAL_PATH]))]))))

    def test_not_macho(self):
        self.assertIsNone(mg.macho_x86_64_dylibs(reader(b'#!/bin/sh\necho hi\n')))
        # A Java class file starts with 0xCAFEBABE followed by its version, not a slice count.
        self.assertIsNone(mg.macho_x86_64_dylibs(reader(struct.pack('>II', mg.FAT_MAGIC, 0x00000034) + b'\0' * 64)))
        self.assertIsNone(mg.macho_x86_64_dylibs(reader(b'')))

    def test_scan_fs(self):
        with tempfile.TemporaryDirectory() as root:
            os.makedirs(f'{root}/usr/bin')
            os.makedirs(f'{root}/System/Library/dyld')
            Path(f'{root}/usr/bin/uses').write_bytes(thin_x86_64([mg.METAL_PATH]).ljust(8192, b'\0'))
            Path(f'{root}/usr/bin/other').write_bytes(thin_x86_64(['/usr/lib/libz.dylib']).ljust(8192, b'\0'))
            Path(f'{root}/usr/bin/armonly').write_bytes(thin_arm64([mg.METAL_PATH]).ljust(8192, b'\0'))
            Path(f'{root}/System/Library/dyld/skipped').write_bytes(thin_x86_64([mg.METAL_PATH]).ljust(8192, b'\0'))
            os.symlink(f'{root}/usr/bin/uses', f'{root}/usr/bin/link')
            found = list(mg.scan_fs(root, {os.path.join(root, 'System/Library/dyld')}))
            self.assertEqual(found, [('/usr/bin/armonly', 'arm64'), ('/usr/bin/uses', 'x86_64')])

    def test_arm64_slice(self):
        data = fat([(0x0100000C, thin_arm64([mg.METAL_PATH]))])
        self.assertEqual(mg.macho_x86_64_dylibs(reader(data), mg.CPU_TYPE_ARM64), [('load', mg.METAL_PATH)])
        self.assertIsNone(mg.macho_x86_64_dylibs(reader(thin_x86_64([mg.METAL_PATH])), mg.CPU_TYPE_ARM64))


class Table(unittest.TestCase):
    METAL = {
        '_OBJC_CLASS_$_MTLTextureDescriptor': '__DATA,__objc_data',
        '_OBJC_CLASS_$_MTLRenderPassDescriptor': '__DATA,__objc_data',
        '_OBJC_METACLASS_$_MTLRenderPassDescriptor': '__DATA,__objc_data',
        '_OBJC_CLASS_$_MTLResidencySetDescriptor': '__DATA,__objc_data',
        '_OBJC_CLASS_$_MTL4CompilerDescriptor': '__DATA,__objc_data',
        '_MTLCopyAllDevices': '__TEXT,__text',
        '_MTLNewThing': '__TEXT,__text',
        '_MTLDeviceWasRemovedNotification': '__DATA_CONST,__const',
    }
    OLD = {'_OBJC_CLASS_$_MTLTextureDescriptor', '_OBJC_CLASS_$_MTLRenderPassDescriptor',
           '_OBJC_METACLASS_$_MTLRenderPassDescriptor', '_MTLCopyAllDevices', '_MTLDeviceWasRemovedNotification'}
    WRAPPER = {'_OBJC_CLASS_$_MTLResidencySetDescriptor'}

    def table(self, refs, replaced=frozenset()):
        return mg.build_table(refs, self.METAL, dict.fromkeys(self.OLD), dict.fromkeys(self.WRAPPER), True, replaced)

    def test_kinds_and_providers(self):
        refs = [
            ('/A', 'cache', '_OBJC_CLASS_$_MTLTextureDescriptor', False, 'symtab'),
            ('/B', 'cache', '_OBJC_METACLASS_$_MTLRenderPassDescriptor', False, 'symtab'),
            ('/B', 'cache', '_OBJC_CLASS_$_MTLRenderPassDescriptor', False, 'objc'),
            ('/A', 'cache', '_OBJC_CLASS_$_MTLResidencySetDescriptor', True, 'symtab'),
            ('/usr/bin/x', 'disk', '_OBJC_CLASS_$_MTL4CompilerDescriptor', False, 'binds'),
            ('/A', 'cache', '_MTLNewThing', False, 'symtab'),
            ('/A', 'cache', '_MTLCopyAllDevices', False, 'symtab'),
            ('/A', 'cache', '_MTLDeviceWasRemovedNotification', False, 'symtab'),
            ('/A', 'cache', '_MTLGoneSymbol', True, 'symtab'),
        ]
        symbols, summary = self.table(refs)
        by = {(s['kind'], s['name']): s for s in symbols}
        rpd = by[('class', 'MTLRenderPassDescriptor')]
        self.assertTrue(rpd['subclassed'])
        self.assertEqual(rpd['sources'], ['objc', 'symtab'])
        self.assertEqual(by[('data', '_MTLDeviceWasRemovedNotification')]['exported_by'],
                         {'metal': True, 'metalold': True, 'moraea_wrapper': False})
        self.assertTrue(by[('data', '_MTLDeviceWasRemovedNotification')]['provided_today'])
        res = by[('class', 'MTLResidencySetDescriptor')]
        self.assertEqual((res['exported_by']['moraea_wrapper'], res['provided_today'], res['importers']['strong']),
                         (True, True, 0))
        self.assertEqual(by[('unknown', '_MTLGoneSymbol')]['exported_by']['metal'], False)
        self.assertEqual(summary['class'], {
            'required': 4, 'required_strong': 3, 'missing_from_metalold': 2, 'moraea_provides': 1,
            'nobody_provides': ['MTL4CompilerDescriptor'],
            'nobody_provides_outside_gpu_driver_bundles': ['MTL4CompilerDescriptor'], 'not_in_metal': []})
        self.assertEqual(summary['function']['nobody_provides'], ['_MTLNewThing'])
        self.assertEqual(summary['data']['missing_from_metalold'], 0)
        self.assertEqual(summary['unknown']['not_in_metal'], ['_MTLGoneSymbol'])

    def test_gpu_driver_bundles_and_ivars(self):
        drv = '/System/Library/Extensions/AGXMetalG13X.bundle/Contents/MacOS/AGXMetalG13X'
        refs = [(drv, 'cache', '_MTLNewThing', False, 'symtab'),
                (drv, 'cache', '_OBJC_IVAR_$__MTLCommandBuffer._queue', False, 'symtab'),
                ('/A', 'cache', '_OBJC_IVAR_$__MTLCommandBuffer._queue', False, 'symtab')]
        metal = dict(self.METAL, **{'_OBJC_IVAR_$__MTLCommandBuffer._queue': '__DATA,__objc_ivar'})
        symbols, summary = mg.build_table(refs, metal, dict.fromkeys(self.OLD), {}, True)
        by = {s['name']: s for s in symbols}
        self.assertEqual(by['_MTLNewThing']['importers']['outside_gpu_driver_bundles'], 0)
        self.assertTrue(by['_OBJC_IVAR_$__MTLCommandBuffer._queue']['ivar_offset'])
        self.assertEqual(by['_OBJC_IVAR_$__MTLCommandBuffer._queue']['importers']['names'], ['/A', drv])
        self.assertEqual(summary['function']['nobody_provides'], ['_MTLNewThing'])
        self.assertEqual(summary['function']['nobody_provides_outside_gpu_driver_bundles'], [])
        self.assertEqual(summary['data_ivar_offsets']['nobody_provides'], ['_OBJC_IVAR_$__MTLCommandBuffer._queue'])
        self.assertEqual(summary['data']['required'], 1)

    def test_weak_only_if_every_reference_is_weak(self):
        refs = [('/A', 'cache', '_MTLNewThing', True, 'symtab'), ('/A', 'cache', '_MTLNewThing', False, 'symtab'),
                ('/B', 'disk', '_MTLNewThing', True, 'binds')]
        (sym,), _ = self.table(refs)
        self.assertEqual((sym['importers']['count'], sym['importers']['strong'], sym['importers']['disk']), (2, 1, 1))
        self.assertEqual(sym['importers']['names'], ['/A', '/B'])

    def test_arm64_extra(self):
        refs = [('/A', 'cache', '_MTLCopyAllDevices', False, 'symtab')]
        symbols, _ = self.table(refs)
        extra = mg.arm64_extra([('/F', ['_MTLCopyAllDevices', '_MTLNewThing', '_OBJC_CLASS_$_MTL4CompilerDescriptor']),
                                ('/G', ['_MTLNewThing']), ('/H', None)],
                               symbols, self.METAL, dict.fromkeys(self.OLD), dict.fromkeys(self.WRAPPER))
        self.assertEqual([(e['name'], e['kind'], e['count']) for e in extra],
                         [('_MTLNewThing', 'function', 2), ('MTL4CompilerDescriptor', 'class', 1)])
        self.assertEqual(extra[1]['exported_by'], {'metal': True, 'metalold': False, 'moraea_wrapper': False})

    def test_names_capped_and_ordered(self):
        refs = [(f'/lib{i}', 'cache', '_MTLNewThing', i % 2 == 0, 'symtab') for i in range(8)]
        (sym,), _ = self.table(refs)
        self.assertEqual(sym['importers']['count'], 8)
        self.assertEqual(sym['importers']['names'], ['/lib1', '/lib3', '/lib5', '/lib7', '/lib0'])

    def test_replaced_importers_do_not_require(self):
        refs = [('/OCLP/MTLCompiler', 'cache', '_MTLNewThing', False, 'symtab'),
                ('/A', 'cache', '_MTLCopyAllDevices', False, 'symtab')]
        symbols, summary = self.table(refs, frozenset({'/OCLP/MTLCompiler'}))
        new = next(s for s in symbols if s['name'] == '_MTLNewThing')
        self.assertFalse(new['required'])
        self.assertEqual(new['importers']['replaced_by_oclp'], ['/OCLP/MTLCompiler'])
        self.assertEqual(summary['function']['required'], 1)
        self.assertEqual(summary['function']['nobody_provides'], [])

    def test_wrapper_without_reexport(self):
        refs = [('/A', 'cache', '_MTLCopyAllDevices', False, 'symtab')]
        (sym,), _ = mg.build_table(refs, self.METAL, dict.fromkeys(self.OLD), {}, False)
        self.assertFalse(sym['provided_today'])


class Overlay(unittest.TestCase):
    def test_replaced_paths(self):
        with tempfile.TemporaryDirectory() as payload:
            base = Path(payload, '13.6-25/System/Library/PrivateFrameworks/MTLCompiler.framework/Versions/A')
            base.mkdir(parents=True)
            (base / 'MTLCompiler').write_bytes(b'x')
            Path(payload, '12.5/usr/libexec').mkdir(parents=True)
            Path(payload, '12.5/usr/libexec/tool').write_bytes(b'x')
            imap = {'entries': [
                {'operation': 'Merge System Volume', 'destination': '/System/Library/PrivateFrameworks/MTLCompiler.framework',
                 'support_package_relative_path': '13.6-25/System/Library/PrivateFrameworks/MTLCompiler.framework'},
                {'operation': 'Overwrite System Volume', 'destination': '/usr/libexec/tool',
                 'support_package_relative_path': '12.5/usr/libexec/tool'},
                {'operation': 'Overwrite System Volume', 'destination': '/usr/libexec/absent',
                 'support_package_relative_path': '12.5/usr/libexec/absent'},
            ]}
            self.assertEqual(mg.overlay_replaced_paths(json.loads(json.dumps(imap)), payload), {
                '/System/Library/PrivateFrameworks/MTLCompiler.framework/Versions/A/MTLCompiler',
                '/usr/libexec/tool'})


if __name__ == '__main__':
    unittest.main()
