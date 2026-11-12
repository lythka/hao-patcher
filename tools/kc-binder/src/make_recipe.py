"""Release-time producers for the kernel binder. These need what the binder must not: the standalone kernel,
llvm-objdump (through tools/kernel-fixes/port_profile.py) and clang.

- `blob`: assemble tools/kernel-foundation/src/bootstrap_pv_alias.S as pv_bootstrap.py does and record it as a
  relocatable template: the code with every rel32 field zeroed and one fixup (offset, symbol) per field.
- `recipe`: for each code point of kc-recipe.json and each IntelCommpage hook, the OpenCore-style mask that hides
  relocated fields, the sha256 of the masked bytes and the (segment, offset) every hidden field must reach; the
  IntelCommpage symbol points. No Apple bytes are written, only masks, hashes and positions. The result is bound
  against a reference Boot KC before it is written.
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import runpy
import struct
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
UNIT = HERE.parent
REPO = UNIT.parents[1]
FOUNDATION = REPO/'tools/kernel-foundation'
PV_SOURCE = FOUNDATION/'src/bootstrap_pv_alias.S'
sys.path.insert(0, str(HERE))
import kcbind   # noqa: E402


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def rel(path):
    return str(Path(path).resolve().relative_to(REPO))


def write(path, value):
    Path(path).write_text(json.dumps(value, indent=1)+'\n')


# PV blob -------------------------------------------------------------------------------------------------------

def assemble(source, origin, values):
    """pv_bootstrap.generate's assembly step for these symbol values; returns the code bytes."""
    macho = runpy.run_path(str(FOUNDATION/'commpage_build.py'))
    lines = ['.text', f'.set origin, {origin:#x}']
    lines += [f'.set {name}, {value:#x}' for name, value in values.items()]
    lines.append(Path(source).read_text())
    with tempfile.TemporaryDirectory(prefix='kc-binder-pv-') as temporary:
        src, obj = Path(temporary)/'pv.S', Path(temporary)/'pv.o'
        src.write_text(('\n'.join(lines)+'\n').replace('pv_alias_start', 'Lpv_alias_start'))
        subprocess.run(['xcrun', 'clang', '-target', 'x86_64-apple-macos10.13', '-c', str(src), '-o', str(obj)],
                       check=True)
        data = obj.read_bytes()
    segment = next(p for c, p in macho['commands'](data) if c == 0x19)
    section = segment+72
    _, size, offset = struct.unpack_from('<QQI', data, section+32)
    if struct.unpack_from('<I', data, section+60)[0]:
        raise ValueError('PV code has relocations')
    return data[offset:offset+size]


def int32(data, at):
    return struct.unpack_from('<i', data, at)[0]


def fields(base, moved, delta):
    """Offsets of the 4-byte fields that change by exactly `delta`; together they must explain every changed
    byte."""
    found = [at for at in range(len(base)-3) if int32(moved, at)-int32(base, at) == delta]
    covered = {at+k for at in found for k in range(4)}
    changed = {i for i, (a, b) in enumerate(zip(base, moved)) if a != b}
    if len(base) != len(moved) or not changed <= covered or any(b-a < 4 for a, b in zip(found, found[1:])):
        raise ValueError('PV fields are not separate rel32 values')
    return found


def make_blob(source, symbols):
    delta = 0x10000
    zero = dict.fromkeys(symbols, 0)
    base = assemble(source, 0, zero)
    fixups = []
    for name in symbols:
        fixups += [dict(at=at, symbol=name) for at in fields(base, assemble(source, 0, dict(zero, **{name: delta})),
                                                            delta)]
    fixups.sort(key=lambda f: f['at'])
    if sorted(fields(base, assemble(source, delta, zero), -delta)) != [f['at'] for f in fixups]:
        raise ValueError('PV origin-relative fields differ from the symbol fields')
    code = bytearray(base)
    for f in fixups:
        if int32(code, f['at']) != -(f['at']+4):
            raise ValueError(f'PV field +{f["at"]:#x} is not symbol - (origin + . + 4)')
        code[f['at']:f['at']+4] = bytes(4)
    blob = dict(format=1, source=dict(path=rel(source), sha256=sha256(Path(source).read_bytes())),
                assembler='xcrun clang -target x86_64-apple-macos10.13 -c (pv_bootstrap.py)',
                rule='at each fixup: int32 = symbol - (origin + at + 4); origin = the cave address',
                symbols=list(symbols), code=bytes(code).hex(), fixups=fixups)
    # The template with arbitrary values must equal a fresh assembly with those values.
    origin = 0xffffff80002f1c00
    values = {n: 0xffffff8000200000+0x1010*i for i, n in enumerate(symbols)}
    if kcbind.link_blob(blob, origin, values) != assemble(source, origin, values):
        raise ValueError('PV template does not reproduce the assembler')
    return blob


# Binder recipe -------------------------------------------------------------------------------------------------

def port_profile():
    spec = importlib.util.spec_from_file_location('port_profile', REPO/'tools/kernel-fixes/port_profile.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def code_check(pp, kernel, address, length, names):
    """{length, mask, masked_sha256, fields} of the kernel code at address."""
    ins = pp.decode(kernel, address, address+length+16)
    if not ins or ins[0].address != address:
        raise ValueError(f'{address:#x} is not an instruction boundary')
    data, keep = pp.masked(ins, address, address+length)
    if len(data) != length:
        raise ValueError(f'{address:#x}: decoded {len(data)} of {length} bytes')
    mask = bytes(0xff if k else 0 for k in keep)
    result = []
    for i in ins:
        for (pos, size), target in zip(i.fields, i.refs):
            at = i.address-address+pos
            if at >= length:
                continue
            point = pp.kc_point(kernel, target)
            result.append(dict(at=at, end=i.address-address+len(i.raw),
                               target=dict(segment=point['segment'], offset=point['offset']),
                               name=names.get(target) or kernel.name_at(target)))
    return dict(length=length, mask=mask.hex(), masked_sha256=sha256(kcbind.masked(data, mask)), fields=result)


def point(pp, kernel, address):
    p = pp.kc_point(kernel, address)
    return dict(segment=p['segment'], offset=p['offset'])


REQUIRED = ['_printf', '_pmap_create_options', '_vm_map_create_options', '_commpage_populate',
            '_commpage_text_populate', '_random', '_commpage_text32_location', '_commpage_text64_location',
            '_mach_make_memory_entry', '_vm_map_enter_mem_object', '_kernel_map', '_vm_protect', '_amfi',
            '_copyin', '_copyinstr', '_vfs_context_current', '_vnode_getfromfd', '_mac_vnop_getxattr', '_vnode_put',
            '_PE_state', '_PE_parse_boot_argn', '_copyout', '_ml_set_interrupts_enabled', '_current_thread',
            '_user_trap', '_machine_thread_get_state', '_fpu_get_fxstate', '_fpu_set_fxstate']
ALIAS_SLOTS = ['_commPagePtr32', '_commPagePtr64', '_commPageTextPtr32', '_commPageTextPtr64']
MAP_SLOTS = ['_commpage32_map', '_commpage64_map', '_commpage_text32_map', '_commpage_text64_map']
HOOK_SYMBOLS = ['_vm_commpage_init', '_vm_commpage_text_init', '_vm_commpage_enter', '_amfi_interface_register']


def make_recipe(kc_recipe_path, profile, blob_path, kernel_path, boot_kc, iosurface_layout):
    """The binder recipe (commpage_build.py's address list and hook order), checked by binding boot_kc."""
    pp = port_profile()
    kernel = pp.Kernel(kernel_path)
    kc_recipe = json.loads(Path(kc_recipe_path).read_text())
    if (kc_recipe['kernel_sha256'], kc_recipe['kernel_uuid']) != (kernel.sha256, kernel.uuid):
        raise ValueError('kc-recipe.json describes another kernel')
    early = kc_recipe['definitions']['early-vm.json']
    template = kc_recipe['early_vm_template']
    names = {}
    for group in ('addresses', 'pv_addresses'):
        for key, value in early[group].items():
            names[int(value['kernel_address'], 0)] = key
    lengths = dict(site=len(template['original'])//2, range_call=len(template['range_call']['original'])//2,
                   pv_hook=len(template['pv_aliases']['hook']['original'])//2)
    code = {item: code_check(pp, kernel, int(early[item]['kernel_address'], 0), length, names)
            for item, length in lengths.items()}
    cave = early['pv_cave']
    code['pv_cave_tail'] = code_check(pp, kernel, int(cave['kernel_address'], 0)+cave['capacity'], cave['tail'], names)
    for item, check in code.items():
        for field in check['fields']:
            key = field['name']
            for group in ('addresses', 'pv_addresses'):
                if key in early[group] and (early[group][key]['segment'], early[group][key]['offset']) != (
                        field['target']['segment'], field['target']['offset']):
                    raise ValueError(f'{item}: field for {key} reaches another point than kc-recipe.json')

    profile = Path(profile)
    exec_entry = json.loads((profile/'commpage-exec-entry.json').read_text())
    fixed = json.loads((profile/'commpage-fixed-entry.json').read_text())
    rename = json.loads((profile/'sandbox-rename-layout.json').read_text())
    for d in (exec_entry, fixed, rename):
        if d['kernel_sha256'] != kernel.sha256:
            raise ValueError('commpage definition belongs to another kernel')
    symbols = {name: point(pp, kernel, kernel.address(name))
               for name in REQUIRED+ALIAS_SLOTS+MAP_SLOTS+['_csr_get_active_config']}
    hooks = [(name, kernel.address(name)) for name in HOOK_SYMBOLS+[exec_entry['symbol']]]
    hooks += [('fixed_locator', int(fixed['address'], 0)), ('_mac_policy_register', kernel.address('_mac_policy_register')),
              ('vn_rename_return', int(rename['return_address'], 0))]
    hook_points = [dict(name=name, **point(pp, kernel, va), **code_check(pp, kernel, va, 14, {}))
                   for name, va in hooks]

    kc = kcbind.Collection(Path(boot_kc).read_bytes())
    layout = json.loads(Path(iosurface_layout).read_text())
    if layout['collection_sha256'] != kc.sha256:
        raise ValueError('IOSurface layout belongs to another collection')
    member = kc.member(layout['identifier'])
    entry = int(layout['entry_address'], 0)
    segment = next(s for s, (base, size, _, _) in member.segments.items() if base <= entry < base+size)
    prefix = bytes.fromhex(layout['prefix_hex'])
    io_entry = dict(segment=segment, offset=hex(entry-member.segments[segment][0]), length=len(prefix),
                    mask=(b'\xff'*len(prefix)).hex(), masked_sha256=sha256(prefix), fields=[])

    recipe = dict(format=kcbind.FORMAT,
                  about='Masks, masked-byte hashes and relocation targets for kc-recipe.json; made by '
                        'tools/kc-binder make-recipe; no Apple bytes',
                  kc_recipe=dict(path=rel(kc_recipe_path), sha256=sha256(Path(kc_recipe_path).read_bytes())),
                  pv_blob=dict(path=rel(blob_path), sha256=sha256(Path(blob_path).read_bytes())),
                  kernel=dict(uuid=kernel.uuid, sha256=kernel.sha256), darwin_version=kernel.darwin,
                  code=code,
                  commpage=dict(required=REQUIRED, alias_slots=ALIAS_SLOTS, map_slots=MAP_SLOTS, symbols=symbols,
                                hooks=hook_points,
                                reads=dict(user_trap=dict(symbol='_user_trap', length=0x70),
                                           csr_get_active_config=dict(symbol='_csr_get_active_config', length=32)),
                                iosurface=dict(member=layout['identifier'], entry=io_entry)))
    return recipe


def self_check(recipe_path, boot_kc):
    recipe = kcbind.Recipe(recipe_path, REPO)
    kc = kcbind.Collection(Path(boot_kc).read_bytes())
    kcbind.bind_repairs(recipe, kc)
    kcbind.commpage_table(recipe, kc)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command', required=True)
    b = sub.add_parser('blob', help='assemble the PV alias code into a relocatable template')
    b.add_argument('--kc-recipe', type=Path, required=True, help='kc-recipe.json (names the PV symbols)')
    b.add_argument('--source', type=Path, default=PV_SOURCE)
    b.add_argument('--out', type=Path, required=True)
    r = sub.add_parser('recipe', help='masks and relocation targets for a kc-recipe.json')
    r.add_argument('--kc-recipe', type=Path, required=True)
    r.add_argument('--profile', type=Path, required=True, help='kernel-fixes profile folder (commpage definitions)')
    r.add_argument('--blob', type=Path, required=True)
    r.add_argument('--kernel', type=Path, required=True, help='the standalone kernel kc-recipe.json describes')
    r.add_argument('--boot-kc', type=Path, required=True, help='reference Boot KC (IOSurface member; self-check)')
    r.add_argument('--iosurface-layout', type=Path, required=True, help='iosurface-layout.json of --boot-kc')
    r.add_argument('--out', type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == 'blob':
        symbols = list(json.loads(args.kc_recipe.read_text())['definitions']['early-vm.json']['pv_addresses'])
        write(args.out, make_blob(args.source, symbols))
    else:
        write(args.out, make_recipe(args.kc_recipe, args.profile, args.blob, args.kernel, args.boot_kc,
                                    args.iosurface_layout))
        self_check(args.out, args.boot_kc)
    print(args.out)


if __name__ == '__main__':
    main()
