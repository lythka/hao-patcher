"""Derive one build's filter profile lines from its x86_64 SkyLight and QuartzCore.

    python3 -I derive.py --build BUILD --dsc dyld_shared_cache_x86_64
    python3 -I derive.py --build BUILD --skylight SkyLight --quartzcore QuartzCore

--dsc extracts both libraries with `ipsw dyld macho -x` (extracted libraries keep
their local symbols). Every value is found by symbol name and checked against the
instruction that uses it; nothing is taken from another build. The output is the
two lines to add to src/profiles.h. A value that cannot be found stops with
"MISSING: <what>".
"""
import argparse
from pathlib import Path
import re
import struct
import subprocess
import sys
import tempfile

API_TABLE = '_quartzcore_vtable'
DISPLAY_CALLBACKS = '__ZL35metal_core_animation_display_vtable'
SOFTWARE_TRAVERSE = '__ZL19layer_tile_traverseP11CGTileStateP22WSCompositeDestinationP22WSCompositeSourceLayer'
API_INIT = '_WSInitialize'
LEGACY_RENDER = '___MetalCompositeLayer_block_invoke'
RENDER = '_CARenderOGLRenderDisplayWithOptions'
ADDED = '_CARenderUpdateAddedAllContexts'
DISPLAY_VTABLE = '__ZTVN2CA12WindowServer7DisplayE'
INVERSE = '__ZNK2CA12WindowServer7Display20actual_inverse_colorEv'
FILTER = '__ZN2CA3OGLL18prepare_one_filterERPNS0_6FilterERNS0_8RendererERNS0_5LayerEPKNS_6Render6FilterEbbRNS0_17SourceRequirementE'
CLIP = '_CARenderUpdateClipBackdropInvalidations2'
FINALIZE = '__ZN2CA6Render6Update31finalize_backdrop_invalidationsEb'
ROI = '__ZN2CA3OGL18prepare_layers_roiERNS0_8RendererEPNS0_5LayerERKNS0_6GstateEPS4_'


class Missing(Exception):
    pass


def need(value, what):
    if value is None or value == [] or value == {}:
        raise Missing(what)
    return value


def tool(*args):
    return subprocess.run(list(map(str, args)), check=True, capture_output=True, text=True).stdout


class Image:
    def __init__(self, path):
        self.path = Path(path)
        self.data = self.path.read_bytes()
        magic, cpu, _, _, ncmds, _ = struct.unpack_from('<6I', self.data, 0)
        if magic != 0xfeedfacf or cpu != 0x1000007:
            raise Missing(f'x86_64 Mach-O at {path}')
        self.uuid, self.base, self.segments, at = None, None, [], 32
        for _ in range(ncmds):
            cmd, size = struct.unpack_from('<II', self.data, at)
            if cmd == 0x1b:
                self.uuid = self.data[at+8:at+24]
            elif cmd == 0x19:
                name = self.data[at+8:at+24].rstrip(b'\0')
                vmaddr, vmsize, fileoff, filesize = struct.unpack_from('<4Q', self.data, at+24)
                self.segments.append((vmaddr, filesize, fileoff))
                if name == b'__TEXT':
                    self.base = vmaddr
            at += size
        need(self.uuid, f'LC_UUID in {path}')
        need(self.base, f'__TEXT in {path}')
        self.symbols = {}
        for line in tool('nm', self.path).splitlines():
            parts = line.split()
            if len(parts) == 3:
                self.symbols.setdefault(parts[2], int(parts[0], 16))
        self.stubs = {}
        for line in tool('otool', '-Iv', self.path).splitlines():
            parts = line.split()
            if len(parts) == 3 and parts[0].startswith('0x'):
                self.stubs[int(parts[0], 16)] = parts[2]

    def symbol(self, name):
        return need(self.symbols.get(name), f'symbol {name} in {self.path.name}')

    def stub(self, name):
        found = [address for address, target in self.stubs.items() if target == name]
        return need(found, f'stub for {name} in {self.path.name}')

    def pointer(self, address):
        for vmaddr, filesize, fileoff in self.segments:
            if vmaddr <= address < vmaddr+filesize:
                return struct.unpack_from('<Q', self.data, fileoff+address-vmaddr)[0]
        raise Missing(f'mapped pointer at {address:#x} in {self.path.name}')

    def code(self, name):
        self.symbol(name)
        out = tool('xcrun', 'llvm-objdump', '-d', '--no-show-raw-insn', '--disassemble-symbols='+name, self.path)
        rows = []
        for line in out.splitlines():
            match = re.match(r'\s*([0-9a-f]+):\s+(\S+)\s*(.*)', line)
            if match:
                rows.append((int(match[1], 16), match[2], match[3].split('##')[0].strip(), line))
        return need(rows, f'code of {name} in {self.path.name}')


def rip_target(text):
    match = re.search(r'## 0x([0-9a-f]+)', text)
    return int(match[1], 16) if match else None


def call_target(operands):
    match = re.match(r'0x([0-9a-f]+)', operands)
    return int(match[1], 16) if match else None


def skylight_profile(sky):
    api = sky.symbol(API_TABLE)
    callbacks = sky.symbol(DISPLAY_CALLBACKS)
    # WSInitialize fills the API table entry by entry until a byte count: cmpq $N, %reg.
    rows = sky.code(API_INIT)
    count = None
    for i, (_, op, _, line) in enumerate(rows):
        if op == 'leaq' and rip_target(line) == api:
            for _, op2, args, _ in rows[i+1:i+20]:
                match = re.match(r'\$0x([0-9a-f]+), %r', args)
                if op2 == 'cmpq' and match:
                    count = int(match[1], 16) // 8
                    break
    need(count, f'{API_INIT} loop that fills {API_TABLE}')
    # The legacy render call passes the display callbacks in %rdx.
    renders = set(sky.stub(RENDER))
    rows = sky.code(LEGACY_RENDER)
    bound = any(op == 'callq' and call_target(args) in renders and
                any(op2 == 'leaq' and rip_target(line) == callbacks and args2.endswith('%rdx')
                    for _, op2, args2, line in rows[max(0, i-6):i])
                for i, (_, op, args, _) in enumerate(rows))
    if not bound:
        raise Missing(f'{RENDER} call with {DISPLAY_CALLBACKS} in {LEGACY_RENDER}')
    added = set(sky.stub(ADDED))
    rows = sky.code(SOFTWARE_TRAVERSE)
    returns = [rows[i+1][0] for i, (_, op, args, _) in enumerate(rows[:-1])
               if op == 'callq' and call_target(args) in added]
    if len(returns) != 1:
        raise Missing(f'one {ADDED} call in {SOFTWARE_TRAVERSE} (found {len(returns)})')
    return dict(api_table=api-sky.base, api_count=count, display_callbacks=callbacks-sky.base,
                software_update_return=returns[0]-sky.base)


def quartzcore_profile(qc):
    vtable, inverse = qc.symbol(DISPLAY_VTABLE), qc.symbol(INVERSE)
    slots = [i for i in range(512) if qc.pointer(vtable+16+8*i) == inverse]
    if len(slots) != 1:
        raise Missing(f'one {INVERSE} slot in {DISPLAY_VTABLE} (found {len(slots)})')
    slot = slots[0]
    if not any(op == 'callq' and args.startswith(f'*{slot*8:#x}(') for _, op, args, _ in qc.code(FILTER)):
        raise Missing(f'slot {slot} virtual call in prepare_one_filter')
    # Clip tests will_clip_backdrops right before it finalizes: testb $0x1, OFF(%reg); je; ...; callq finalize.
    finalize = qc.symbol(FINALIZE)
    rows = qc.code(CLIP)
    flags = None
    for i, (_, op, args, _) in enumerate(rows):
        if op == 'callq' and call_target(args) == finalize:
            for _, op2, args2, _ in reversed(rows[max(0, i-5):i]):
                match = re.match(r'\$0x1, 0x([0-9a-f]+)\(%r', args2)
                if op2 == 'testb' and match:
                    flags = int(match[1], 16)
                    break
    need(flags, f'will_clip_backdrops test before {FINALIZE} in {CLIP}')
    if not any(op == 'testb' and args.startswith(f'$0x4, {flags:#x}(') for _, op, args, _ in qc.code(ROI)):
        raise Missing(f'backdrop_invalidations_finalized test at {flags:#x} in prepare_layers_roi')
    return dict(inverse_slot=slot, update_flags=flags)


def c_uuid(uuid):
    return 'UUID(' + ','.join(f'0x{b:02x}' for b in uuid) + ')'


def text_uuid(uuid):
    h = uuid.hex().upper()
    return f'{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:]}'


def lines(build, sky, qc):
    s, q = skylight_profile(sky), quartzcore_profile(qc)
    return [f'SKYLIGHT_PROFILE("{build}", {c_uuid(sky.uuid)}, {s["api_table"]:#x}, {s["api_count"]}, '
            f'{s["display_callbacks"]:#x}, {s["software_update_return"]:#x}) // {text_uuid(sky.uuid)}',
            f'QUARTZCORE_PROFILE("{build}", {c_uuid(qc.uuid)}, {q["inverse_slot"]}, {q["update_flags"]:#x})'
            f' // {text_uuid(qc.uuid)}']


def derive(build, dsc=None, skylight=None, quartzcore=None):
    with tempfile.TemporaryDirectory(prefix='ws-filter-derive-') as tmp:
        if dsc:
            for name in ('SkyLight', 'QuartzCore'):
                tool('ipsw', 'dyld', 'macho', dsc, name, '-x', '--output', tmp)
            skylight, quartzcore = Path(tmp)/'SkyLight', Path(tmp)/'QuartzCore'
        return lines(build, Image(skylight), Image(quartzcore))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--build', required=True, help='macOS build the libraries come from, e.g. 26B131')
    parser.add_argument('--dsc', help='x86_64 dyld shared cache (main file)')
    parser.add_argument('--skylight', help='extracted x86_64 SkyLight')
    parser.add_argument('--quartzcore', help='extracted x86_64 QuartzCore')
    args = parser.parse_args()
    if not args.dsc and not (args.skylight and args.quartzcore):
        parser.error('give --dsc, or --skylight and --quartzcore')
    try:
        print('\n'.join(derive(args.build, args.dsc, args.skylight, args.quartzcore)))
    except Missing as error:
        sys.exit(f'MISSING: {error}')


if __name__ == '__main__':
    main()
