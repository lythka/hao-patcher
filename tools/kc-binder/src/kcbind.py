"""Bind the Darwin 27 early-VM kernel repairs and the IntelCommpage address table to a Boot kernel collection.

Pure Python (standard library only): no compiler, disassembler or Apple tool. Inputs:

- `kc-recipe.json` (tools/kernel-fixes/profiles/BUILD): every repair point as (kernel segment, offset).
- a binder recipe (recipes/BUILD.json, made by make_recipe.py): per code point the OpenCore-style mask, the sha256
  of the masked bytes and the target of every masked (relocated) field; the IntelCommpage points.
- the PV blob (blobs/pv-alias.json): the bootstrap PV alias code with its rel32 fixups.
- the Boot KC bytes.

Every point resolves through the kernel fileset member's own segment placement. Before a patch is emitted its
bytes must match the masked sha256, every relocated field must reach the recorded target, and every Find must
occur exactly once in the collection, at the resolved point.
"""
import hashlib
import json
from pathlib import Path
import plistlib
import re
import struct

LC_SEGMENT_64, LC_UUID, LC_FILESET_ENTRY = 0x19, 0x1b, 0x80000035
KERNEL_MEMBER = 'com.apple.kernel'
FORMAT = 1


class BindError(ValueError):
    """The collection does not satisfy the recipe; the message names the point."""


def sha256(data):
    return hashlib.sha256(data).hexdigest()


# Mach-O --------------------------------------------------------------------------------------------------------

def commands(data, offset=0):
    if len(data) < offset+32 or struct.unpack_from('<II', data, offset) != (0xfeedfacf, 0x1000007):
        raise BindError(f'no thin x86_64 Mach-O header at {offset:#x}')
    count, size = struct.unpack_from('<II', data, offset+16)
    pos, end = offset+32, offset+32+size
    for _ in range(count):
        cmd, length = struct.unpack_from('<II', data, pos)
        if length < 8 or pos+length > end:
            raise BindError(f'bad load command size at {pos:#x}')
        yield cmd, pos, length
        pos += length


class Member:
    """One fileset entry: its header placement, UUID and segments {name: (vmaddr, vmsize, fileoff, filesize)}."""

    def __init__(self, data, name, address, offset):
        self.name, self.address, self.offset = name, address, offset
        self.segments, self.uuid = {}, None
        for cmd, p, _ in commands(data, offset):
            if cmd == LC_SEGMENT_64:
                label = data[p+8:p+24].split(b'\0')[0].decode()
                if label in self.segments:
                    raise BindError(f'{name}: duplicate segment {label}')
                self.segments[label] = struct.unpack_from('<QQQQ', data, p+24)
            elif cmd == LC_UUID:
                self.uuid = data[p+8:p+24]

    def address_of(self, segment, offset):
        if segment not in self.segments:
            raise BindError(f'{self.name} has no segment {segment}')
        base, size, _, _ = self.segments[segment]
        if not 0 <= offset < size:
            raise BindError(f'{self.name} {segment}+{offset:#x} is outside the segment ({size:#x})')
        return base+offset

    def fileoff(self, va, length=1):
        for base, _, off, filesize in self.segments.values():
            if base <= va and va+length <= base+filesize:
                return off+va-base
        raise BindError(f'{self.name}: {va:#x}+{length} is not backed by file bytes')


class Collection:
    def __init__(self, data):
        self.data = bytes(data)
        self.sha256 = sha256(self.data)
        self.members = {}
        for cmd, p, length in commands(self.data):
            if cmd == LC_FILESET_ENTRY:
                address, offset, name_offset = struct.unpack_from('<QQI', self.data, p+8)
                name = self.data[p+name_offset:p+length].split(b'\0', 1)[0].decode()
                if not name or name in self.members:
                    raise BindError('missing or duplicate fileset entry name')
                self.members[name] = Member(self.data, name, address, offset)
        if KERNEL_MEMBER not in self.members:
            raise BindError('the collection has no com.apple.kernel fileset entry')
        self.kernel = self.members[KERNEL_MEMBER]

    def member(self, name):
        if name not in self.members:
            raise BindError(f'the collection has no fileset member {name}')
        return self.members[name]

    def darwin_version(self):
        found = set()
        for _, _, off, filesize in self.kernel.segments.values():
            for m in re.finditer(rb'Darwin Kernel Version ([0-9]+\.[0-9]+\.[0-9]+):', self.data[off:off+filesize]):
                found.add(m.group(1).decode())
        if len(found) != 1:
            raise BindError(f'kernel member has {len(found)} distinct Darwin versions')
        return found.pop()


# Points --------------------------------------------------------------------------------------------------------

class Bound:
    """A recipe point placed in one collection."""

    def __init__(self, kc, point, member=KERNEL_MEMBER):
        self.member = kc.member(point.get('member', member))
        self.segment, self.offset = point['segment'], int(point['offset'], 0)
        self.address = self.member.address_of(self.segment, self.offset)

    def read(self, kc, length):
        start = self.member.fileoff(self.address, length)
        return start, kc.data[start:start+length]

    def where(self):
        return dict(member=self.member.name, segment=self.segment, offset=hex(self.offset), address=hex(self.address))


def masked(data, mask):
    return bytes(b & m for b, m in zip(data, mask))


def check_code(kc, item, point, check):
    """Read `check['length']` bytes at point, compare them under the mask and follow every relocated field.
    Returns (address, file offset, bytes)."""
    bound = Bound(kc, point)
    length = check['length']
    mask = bytes.fromhex(check['mask'])
    if len(mask) != length:
        raise BindError(f'{item}: recipe mask has {len(mask)} bytes, length is {length}')
    start, raw = bound.read(kc, length)
    if sha256(masked(raw, mask)) != check['masked_sha256']:
        raise BindError(f'{item}: bytes at {bound.address:#x} differ from the recipe (masked)')
    for field in check['fields']:
        at, end = field['at'], field['end']
        if any(mask[at:min(at+4, length)]):
            raise BindError(f'{item}: field +{at:#x} is not masked')
        position = bound.member.fileoff(bound.address+at, 4)
        target = bound.address+end+struct.unpack_from('<i', kc.data, position)[0]
        expected = Bound(kc, field['target']).address
        if target != expected:
            raise BindError(f'{item}: field +{at:#x} reaches {target:#x}, not {field.get("name") or "its target"} '
                            f'{expected:#x}')
    return bound.address, start, raw


# Recipes -------------------------------------------------------------------------------------------------------

def load_json(path):
    return json.loads(Path(path).read_text())


class Recipe:
    """A binder recipe with the kc-recipe.json and PV blob it names, each checked by sha256."""

    def __init__(self, path, root):
        self.path, self.root = Path(path), Path(root)
        self.binder = load_json(self.path)
        if self.binder.get('format') != FORMAT:
            raise BindError(f'{self.path}: unsupported binder recipe format')
        self.kc_recipe = self._pinned(self.binder['kc_recipe'])
        self.blob = self._pinned(self.binder['pv_blob'])
        self.early = self.kc_recipe['definitions']['early-vm.json']
        self.template = self.kc_recipe['early_vm_template']

    def _pinned(self, reference):
        data = (self.root/reference['path']).read_bytes()
        if sha256(data) != reference['sha256']:
            raise BindError(f'{reference["path"]} differs from the sha256 the binder recipe names')
        return json.loads(data)


def select(recipes, kc):
    uuid = kc.kernel.uuid.hex() if kc.kernel.uuid else None
    found = [r for r in recipes if r.kc_recipe['kernel_uuid'] == uuid]
    if len(found) != 1:
        raise BindError(f'MISSING: {len(found)} binder recipes for kernel {uuid}')
    return found[0]


# PV blob -------------------------------------------------------------------------------------------------------

def link_blob(blob, origin, addresses):
    """The PV code for `origin` with every rel32 fixup filled: value = symbol - (origin + at + 4)."""
    code = bytearray.fromhex(blob['code'])
    for fixup in blob['fixups']:
        at, symbol = fixup['at'], fixup['symbol']
        if symbol not in addresses:
            raise BindError(f'PV blob needs {symbol}')
        if code[at:at+4] != bytes(4):
            raise BindError(f'PV blob fixup +{at:#x} is not zero in the template')
        value = addresses[symbol]-(origin+at+4)
        if not -2**31 <= value < 2**31:
            raise BindError(f'PV blob fixup {symbol} is out of rel32 range')
        code[at:at+4] = struct.pack('<i', value)
    return bytes(code)


# Repairs -------------------------------------------------------------------------------------------------------

COMMENTS = dict(site='Initialize early VM range before reservation',
                range_call='Pass early kernel-map reservation bounds in ascending order',
                pv_code='Preserve bootstrap physical-page aliases: code',
                pv_hook='Preserve bootstrap physical-page aliases: registration')
REGISTERS = dict(rax=0, rcx=1, rdx=2, rbx=3, rsp=4, rbp=5, rsi=6, rdi=7,
                 r8=8, r9=9, r10=10, r11=11, r12=12, r13=13, r14=14, r15=15)


def mov(source, destination):
    src, dst = REGISTERS[source], REGISTERS[destination]
    return bytes([0x48 | ((src >> 3) << 2) | (dst >> 3), 0x89, 0xc0 | ((src & 7) << 3) | (dst & 7)])


def site_code(site, original_length, addresses, reservations):
    """The early VM initializer: pmap_virtual_space, the three reservations, the stolen-data base, ret."""
    code = bytearray()

    def relative(prefix, destination, suffix=b''):
        displacement = destination-(site+len(code)+len(prefix)+4+len(suffix))
        code.extend(prefix+struct.pack('<i', displacement)+suffix)

    code.extend(b'\x50')                                    # push rax (startup callbacks enter rsp % 16 == 8)
    relative(b'\x48\x8d\x3d', addresses['virtual_space_start'])
    relative(b'\x48\x8d\x35', addresses['virtual_space_end'])
    relative(b'\xe8', addresses['pmap_virtual_space'])
    code.extend(b'\x58')                                    # pop rax
    for name, size in reservations.items():
        relative(b'\x48\xc7\x05', addresses[name], struct.pack('<I', size))
    code.extend(b'\xb8\xff\x0f\x00\x00')                  # eax = page mask
    relative(b'\x48\x03\x05', addresses['virtual_space_start'])
    branch = len(code)
    code.extend(b'\x72\x00')                                # jc overflow
    code.extend(b'\x48\x25\x00\xf0\xff\xff')              # round up to 4 KiB
    relative(b'\x48\x89\x05', addresses['map_stolen_data'])
    code.extend(b'\x48\x05'+struct.pack('<I', sum(reservations.values())))
    relative(b'\x48\x89\x05', addresses['virtual_space_start'])
    code.extend(b'\xc3')                                    # ret
    code[branch+1] = len(code)-(branch+2)
    code.extend(b'\x0f\x0b')                                # overflow stays fatal
    if len(code) > original_length:
        raise BindError('site: the repair does not fit the initializer')
    return bytes(code)+b'\x90'*(original_length-len(code))


def patch(comment, find, replace, darwin):
    return dict(Arch='x86_64', Base='', Comment=comment, Count=1, Enabled=True, Find=find, Identifier='kernel',
                Limit=0, Mask=b'', MaxKernel=darwin, MinKernel=darwin, Replace=replace, ReplaceMask=b'', Skip=0)


COMMPAGE_ADD = dict(Arch='x86_64', BundlePath='IntelCommpage.kext', Comment='Darwin 27 Intel compatibility',
                    Enabled=True, ExecutablePath='Contents/MacOS/IntelCommpage', PlistPath='Contents/Info.plist',
                    MinKernel='27.0.0', MaxKernel='27.99.99')


def bind_repairs(recipe, kc):
    """(OpenCore Kernel.Patch entries, the early-VM capture they were computed from, per-point report)."""
    if kc.kernel.uuid is None or kc.kernel.uuid.hex() != recipe.kc_recipe['kernel_uuid']:
        raise BindError('the collection kernel is not the kc-recipe kernel')
    early, template, checks = recipe.early, recipe.template, recipe.binder['code']
    darwin = kc.darwin_version()
    if darwin != recipe.binder['darwin_version']:
        raise BindError(f'kernel member is Darwin {darwin}, the recipe is for {recipe.binder["darwin_version"]}')
    lengths = dict(site=len(template['original'])//2, range_call=len(template['range_call']['original'])//2,
                   pv_hook=len(template['pv_aliases']['hook']['original'])//2)
    report, read = {}, {}
    for item, length in lengths.items():
        if checks[item]['length'] != length:
            raise BindError(f'{item}: binder recipe length differs from the kc-recipe template')
        read[item] = check_code(kc, item, early[item], checks[item])
        report[item] = dict(Bound(kc, early[item]).where(), length=length, fileoff=hex(read[item][1]),
                            fields=len(checks[item]['fields']))
    addresses = {k: Bound(kc, v).address for k, v in early['addresses'].items()}
    pv_addresses = {k: Bound(kc, v).address for k, v in early['pv_addresses'].items()}
    for group, values in (('addresses', addresses), ('pv_addresses', pv_addresses)):
        for k, v in values.items():
            report[f'{group}.{k}'] = dict(Bound(kc, early[group][k]).where())

    cave = early['pv_cave']
    capacity, tail = cave['capacity'], cave['tail']
    if checks['pv_cave_tail']['length'] != tail:
        raise BindError('pv_cave_tail: binder recipe length differs from kc-recipe tail')
    cave_bound = Bound(kc, cave)
    cave_start, cave_bytes = cave_bound.read(kc, capacity+tail)
    if any(cave_bytes[:capacity]):
        raise BindError(f'pv_cave: {capacity} bytes at {cave_bound.address:#x} are not zero padding')
    tail_point = dict(segment=cave['segment'], offset=hex(int(cave['offset'], 0)+capacity))
    check_code(kc, 'pv_cave_tail', tail_point, checks['pv_cave_tail'])
    report['pv_cave'] = dict(cave_bound.where(), capacity=capacity, tail=tail, fileoff=hex(cave_start))

    site_va, site_off, site_raw = read['site']
    range_va, range_off, range_raw = read['range_call']
    hook_va, hook_off, hook_raw = read['pv_hook']
    capture = json.loads(json.dumps(template))
    capture.update(kernel_uuid=recipe.kc_recipe['kernel_uuid'], collection_sha256=kc.sha256, darwin_version=darwin,
                   site_address=hex(site_va), site_offset=hex(site_off), original=site_raw.hex(),
                   addresses={k: hex(v) for k, v in addresses.items()})
    capture['range_call'].update(address=hex(range_va), offset=hex(range_off), original=range_raw.hex())
    capture['pv_aliases']['hook'].update(address=hex(hook_va), offset=hex(hook_off), original=hook_raw.hex())
    capture['pv_aliases']['cave'].update(address=hex(cave_bound.address), offset=hex(cave_start),
                                         original=cave_bytes.hex())
    capture['pv_aliases']['addresses'] = {k: hex(v) for k, v in pv_addresses.items()}
    capture['evidence'] = {'source': template.get('evidence'),
                           'port': 'kernel-fixes port-profile: positions from kc-recipe.json, bytes from the Boot KC'}

    patches = [patch(COMMENTS['site'], site_raw,
                     site_code(site_va, len(site_raw), addresses, template['reservations']), darwin)]
    call = template['range_call']
    wrong = mov(call['upper'], 'rsi')+mov(call['lower'], 'rdx')
    right = mov(call['lower'], 'rsi')+mov(call['upper'], 'rdx')
    if range_raw[:len(wrong)] != wrong:
        raise BindError('range_call: the call does not pass reversed bounds')
    patches.append(patch(COMMENTS['range_call'], range_raw, right+range_raw[len(wrong):], darwin))
    code = link_blob(recipe.blob, cave_bound.address, pv_addresses)
    if len(code) > capacity:
        raise BindError('pv_cave: the PV code exceeds the padding')
    patches.append(patch(COMMENTS['pv_code'], cave_bytes, code+cave_bytes[len(code):], darwin))
    size = template['pv_aliases']['hook']['overwrite_size']
    displacement = cave_bound.address-hook_va-5
    if hook_raw[0] != 0xe8 or size < 5:
        raise BindError('pv_hook: the registration point is not a call')
    patches.append(patch(COMMENTS['pv_hook'], hook_raw,
                         b'\xe8'+struct.pack('<i', displacement)+b'\x90'*(size-5)+hook_raw[size:], darwin))

    for entry, offset in zip(patches, (site_off, range_off, cave_start, hook_off)):
        find = entry['Find']
        if len(entry['Replace']) != len(find):
            raise BindError(f'{entry["Comment"]}: Replace and Find lengths differ')
        if kc.data.count(find) != 1 or kc.data.find(find) != offset:
            raise BindError(f'{entry["Comment"]}: Find is not unique in the collection at its point')
    return patches, capture, report


def config_plist(patches):
    """The repair artifact's EFI/OC/config.plist, as tools/kernel-fixes build-repairs writes it."""
    return plistlib.dumps(dict(Kernel=dict(Patch=patches, Add=[dict(COMMPAGE_ADD)])))


# IntelCommpage table -------------------------------------------------------------------------------------------

def commpage_table(recipe, kc):
    """The collection addresses and bytes IntelCommpage is compiled against today (commpage_abi.hpp), each with
    its (member, segment, offset) point."""
    spec = recipe.binder['commpage']
    table = dict(required=list(spec['required']), symbols={}, alias_slots=[], map_slots=[], hooks=[])
    for name, point in spec['symbols'].items():
        table['symbols'][name] = Bound(kc, point).where()
    for key in ('alias_slots', 'map_slots'):
        table[key] = [dict(Bound(kc, spec['symbols'][name]).where(), name=name) for name in spec[key]]
    for hook in spec['hooks']:
        _, _, raw = check_code(kc, 'commpage hook '+hook['name'], hook, hook)
        table['hooks'].append(dict(Bound(kc, hook).where(), name=hook['name'], original=raw.hex()))
    io = spec['iosurface']
    member = kc.member(io['member'])
    entry = dict(io['entry'], member=io['member'])
    _, _, prefix = check_code(kc, 'IOSurface entry', entry, io['entry'])
    table['iosurface'] = dict(member=io['member'], header=hex(member.address), uuid=member.uuid.hex(),
                              entry=Bound(kc, entry).where(), prefix=prefix.hex())
    table['kernel'] = dict(header=hex(kc.kernel.address), uuid=kc.kernel.uuid.hex())
    for key in ('user_trap', 'csr_get_active_config'):
        name = spec['reads'][key]['symbol']
        bound = Bound(kc, spec['symbols'][name])
        table[key] = dict(bound.where(), symbol=name,
                          bytes=bound.read(kc, spec['reads'][key]['length'])[1].hex())
    return table


def abi_lines(table, foundation):
    """The collection-dependent commpage_abi.hpp definitions, worded as commpage_build.py writes them.
    `foundation` is tools/kernel-foundation/commpage_build.py's namespace (user_trap_binding, csr_layout)."""
    a = lambda point: int(point['address'], 0)  # noqa: E731
    lines = {f'addr{name}': f'static constexpr uintptr_t addr{name} = {a(p):#x}ULL;'
             for name, p in ((n, table['symbols'][n]) for n in table['required'])}
    io = table['iosurface']
    lines['addr_iosurface_header'] = f'static constexpr uintptr_t addr_iosurface_header = {io["header"]}ULL;'
    lines['addr_iosurface_set_value'] = (f'static constexpr uintptr_t addr_iosurface_set_value = '
                                         f'{io["entry"]["address"]}ULL;')
    lines['iosurface_uuid'] = ('static const unsigned char iosurface_uuid[] = {'
                               + ','.join(str(v) for v in bytes.fromhex(io['uuid']))+'};')
    lines['iosurface_prefix'] = ('static const unsigned char iosurface_prefix[] = {'
                                 + ','.join(str(v) for v in bytes.fromhex(io['prefix']))+'};')
    user_trap = foundation['user_trap_binding'](bytes.fromhex(table['user_trap']['bytes']))
    lines['user_trap_prefix_length'] = f'static constexpr unsigned user_trap_prefix_length = {len(user_trap)};'
    lines['user_trap_prefix'] = ('static const unsigned char user_trap_prefix[] = {'
                                 + ','.join(str(v) for v in user_trap)+'};')
    csr = table['csr_get_active_config']
    bootargs, csr_offset = foundation['csr_layout'](bytes.fromhex(csr['bytes']), a(csr),
                                                    a(table['symbols']['_PE_state']))
    lines['pe_state_bootargs_offset'] = f'static constexpr uintptr_t pe_state_bootargs_offset = {bootargs:#x};'
    lines['boot_args_csr_offset'] = f'static constexpr uintptr_t boot_args_csr_offset = {csr_offset:#x};'
    lines['addr_kernel_header'] = f'static constexpr uintptr_t addr_kernel_header = {table["kernel"]["header"]}ULL;'
    for key in ('alias_slots', 'map_slots'):
        lines[key] = (f'static const uintptr_t {key}[] = {{'
                      + ','.join(f'{a(p):#x}ULL' for p in table[key])+'};')
    lines['target_uuid'] = ('static const unsigned char target_uuid[] = {'
                            + ','.join(str(v) for v in bytes.fromhex(table['kernel']['uuid']))+'};')
    lines['hook_sites'] = ('static const uintptr_t hook_sites[] = {'
                           + ','.join(f'{a(h):#x}ULL' for h in table['hooks'])+'};')
    lines['hook_original'] = ('static const unsigned char hook_original[][14] = {'
                              + ','.join('{'+','.join(str(v) for v in bytes.fromhex(h['original']))+'}'
                                         for h in table['hooks'])+'};')
    return lines


DEFINITION = re.compile(r'^static (?:constexpr |const )\S+ (?:\S+ )?([A-Za-z_][A-Za-z0-9_]*)(?:\[\])*')


def header_definitions(text):
    """{name: line} of a commpage_abi.hpp."""
    result = {}
    for line in text.splitlines():
        m = DEFINITION.match(line)
        if m:
            result[m.group(1)] = line
    return result


def table_addresses(table, kc):
    """{address: dict(member, segment, offset)} for every collection address in the ABI header, with the file
    offset as repair_binding.py's abi_bindings record it."""
    points = [table['symbols'][n] for n in table['required']]+table['alias_slots']+table['map_slots']
    points += table['hooks']+[table['iosurface']['entry']]
    result = {}
    for p in points:
        member = kc.member(p['member'])
        result[int(p['address'], 0)] = dict(member=p['member'], segment=p['segment'],
                                            offset=member.fileoff(int(p['address'], 0)))
    for key, name in (('kernel', KERNEL_MEMBER), ('iosurface', table['iosurface']['member'])):
        member = kc.member(name)
        segment = next(s for s, (base, size, _, _) in member.segments.items()
                       if base <= member.address < base+size)
        result[member.address] = dict(member=name, segment=segment, offset=member.fileoff(member.address))
    return result
