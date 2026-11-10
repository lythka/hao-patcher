"""Make the Sandbox operation layout: the target libsandbox's operation table against the donor Sandbox kext's.

The target's operation names come from its x86_64 dyld shared cache (no code from it runs); the donor's from the
kext's _operation_names table. The output JSON lists the target indices the donor has no entry for and binds
the result to the target image (cache UUID and the image's __TEXT hash) and the donor kext (sha256), plus the
donor's profile_init entry that IntelCommpage wraps (commpage-kext --sandbox-op-layout).
"""
import argparse
import datetime
import glob
import hashlib
import json
import mmap
from pathlib import Path
import re
import runpy
import struct
import subprocess

HERE = Path(__file__).resolve().parent
MACHO = runpy.run_path(str(HERE/'commpage_build.py'))
FIRST_MESSAGE_FILTER = 'default-message-filter'


def digest(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(8 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


class Cache:
    """Read-only view of an x86_64 dyld shared cache and its subcaches (slide info v2 pointers)."""

    def __init__(self, main):
        self.maps, self.files = [], []
        for path in [str(main)]+sorted(glob.glob(str(main)+'.[0-9][0-9]')):
            with open(path, 'rb') as f:
                data = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
            self.files.append(data)
            if not data[:16].startswith(b'dyld_v1') or b'x86_64' not in data[:16]:
                raise ValueError(f'{path}: not an x86_64 dyld shared cache')
            offset, count = struct.unpack_from('<II', data, 0x138)
            for i in range(count):
                address, size, fileoff, slide, slide_size = struct.unpack_from('<5Q', data, offset+56*i)
                self.maps.append((address, size, fileoff, data, slide if slide_size else None))
        self.main = self.files[0]
        self.uuid = self.main[0x58:0x68].hex()

    def locate(self, address):
        for base, size, fileoff, data, slide in self.maps:
            if base <= address < base+size:
                return data, fileoff+address-base, slide
        raise KeyError(f'{address:#x} outside the cache')

    def read(self, address, size):
        data, offset, _ = self.locate(address)
        return data[offset:offset+size]

    def pointer(self, address):
        data, offset, slide = self.locate(address)
        raw = struct.unpack_from('<Q', data, offset)[0]
        if slide is None:
            return raw
        version, _, _, _, _, _, delta_mask, value_add = struct.unpack_from('<6IQQ', data, slide)
        if version != 2:
            raise ValueError(f'slide info v{version} is not handled')
        value = raw & ~delta_mask
        return value+value_add if value else 0

    def image(self, path):
        offset, count = struct.unpack_from('<II', self.main, 0x1c0)
        for i in range(count):
            address, _, _, name = struct.unpack_from('<QQQI', self.main, offset+32*i)
            if self.main[name:self.main.find(b'\0', name)].decode() == path:
                return address
        raise KeyError(f'{path} is not in the cache')


def segments_at(read, address):
    """Segments and sections of a Mach-O header read through `read(address, size)`."""
    header = read(address, 32)
    magic, cpu, _, _, ncmds, sizeofcmds = struct.unpack_from('<6I', header)
    if (magic, cpu) != (0xfeedfacf, 0x1000007):
        raise ValueError('expected an x86_64 Mach-O image')
    commands = read(address+32, sizeofcmds)
    segments, sections, pos = {}, {}, 0
    for _ in range(ncmds):
        cmd, size = struct.unpack_from('<II', commands, pos)
        if cmd == 0x19:
            name = commands[pos+8:pos+24].split(b'\0')[0].decode()
            segments[name] = struct.unpack_from('<QQ', commands, pos+24)
            for k in range(struct.unpack_from('<I', commands, pos+64)[0]):
                q = pos+72+80*k
                sections[(name, commands[q:q+16].split(b'\0')[0].decode())] = struct.unpack_from('<QQ', commands, q+32)
        pos += size
    return segments, sections


def string_at(read, address, text):
    base, size = text
    if not base <= address < base+size:
        return None
    raw = read(address, min(128, base+size-address))
    end = raw.find(b'\0')
    value = raw[:end] if end > 0 else b''
    return value.decode() if value and all(32 < c < 127 for c in value) else None


def pointer_table(pointer, read, start, text):
    """NULL-terminated table of C-string pointers; any other entry ends it as an error."""
    names, address = [], start
    while True:
        value = pointer(address)
        if value == 0:
            return names
        name = string_at(read, value, text)
        if name is None:
            raise ValueError(f'operation table entry {len(names)} at {address:#x} is not a name')
        names.append(name)
        address += 8


def target_names(cache, image):
    """The operation names of libsandbox in the cache: the pointer table that starts like the donor's."""
    base = cache.image(image)
    segments, sections = segments_at(cache.read, base)
    text = segments['__TEXT']
    found = []
    for (segment, _), (address, size) in sections.items():
        if not segment.startswith('__DATA'):
            continue
        for offset in range(0, size-8, 8):
            try:
                first = string_at(cache.read, cache.pointer(address+offset), text)
            except KeyError:
                continue
            if first == 'default' and string_at(cache.read, cache.pointer(address+offset+8), text) == 'appleevent-send':
                found.append(address+offset)
    if len(found) != 1:
        raise ValueError(f'expected one operation name table in {image}, found {len(found)}')
    names = pointer_table(cache.pointer, cache.read, found[0], text)
    return dict(image=image, image_address=hex(base), names_address=hex(found[0]),
                text_sha256=hashlib.sha256(cache.read(*text)).hexdigest(), names=names)


def donor_identity(kext):
    """Operation names, operation count, filter range and profile_init entry of the donor Sandbox kext."""
    symbols = MACHO['symbols'](kext)
    segments = MACHO['segments'](kext)
    text_base = segments[b'__TEXT'][0]

    def read(address, size):
        for base, length, offset, _ in segments.values():
            if base <= address < base+length:
                return kext[offset+address-base:offset+address-base+size]
        raise KeyError(f'{address:#x} outside the kext')

    def pointer(address):
        return struct.unpack('<Q', read(address, 8))[0]

    tables = [n for n in symbols if re.fullmatch(r'_operation_names(\.\d+)?', n)]
    if len(tables) != 1:
        raise ValueError(f'expected one _operation_names symbol, found {tables}')
    text = (text_base, segments[b'__TEXT'][1])
    names = pointer_table(pointer, read, symbols[tables[0]], text)
    ordered = sorted(symbols.values())

    def function(name):
        start = symbols[name]
        end = next(a for a in ordered if a > start)
        return start, read(start, end-start)

    # profile_init: movzbl 0x4(%rsi),%r8d; cmpl $COUNT,%r8d  (the operation-count check)
    start, code = function('_profile_init')
    match = re.search(rb'\x44\x0f\xb6\x46\x04\x41\x81\xf8(....)', code, re.S)
    if not match:
        raise ValueError('profile_init operation-count check not found')
    count = struct.unpack('<I', match.group(1))[0]
    # _eval: andl $0x7f,%r14d; leal -0x1(%r14),%ecx; cmpl $MAX-1,%ecx  (the filter-id range check)
    _, code = function('_eval')
    match = re.search(rb'\x41\x83\xe6\x7f\x41\x8d\x4e\xff\x83\xf9(.)', code, re.S)
    if not match:
        raise ValueError('eval filter-range check not found')
    filter_max = match.group(1)[0]+1
    # The 14-byte jump replaces whole instructions of a frame-setup prologue with no RIP-relative operand.
    prologue = [b'\x55', b'\x48\x89\xe5', b'\x41\x57', b'\x41\x56', b'\x41\x55', b'\x41\x54', b'\x53']
    pos, entry = 0, read(start, 32)
    while pos < 14:
        step = next((len(p) for p in prologue if entry.startswith(p, pos)), 0)
        if not step and entry.startswith(b'\x48\x83\xec', pos):
            step = 4
        if not step:
            raise ValueError(f'profile_init prologue byte {pos} is not a known frame-setup instruction')
        pos += step
    uuid = next(kext[p+8:p+24] for cmd, p in MACHO['commands'](kext) if cmd == 0x1b)
    return dict(uuid=uuid.hex(), names_symbol=tables[0], names=names, operation_count=count, filter_max=filter_max,
                profile_init=dict(symbol='_profile_init', offset=hex(start-text_base), prefix_hex=entry[:pos].hex()))


def mig_entries(pointer, raw, read, start, text):
    """{char *name; u64 index} entries until a NULL name: the kernel-mig-routine names (aliases share an index)."""
    entries, address = [], start
    while True:
        value = pointer(address)
        if value == 0:
            return entries
        name = string_at(read, value, text)
        if name is None:
            raise ValueError(f'kernel-mig-routine entry {len(entries)} at {address:#x} is not a name')
        entries.append((name, raw(address+8) & 0xffffffff))
        address += 16


def target_mig(cache, image):
    """libsandbox's kernel-mig-routine table: the {name, index} array whose first name is host_info."""
    base = cache.image(image)
    segments, sections = segments_at(cache.read, base)
    text = segments['__TEXT']
    found = []
    for (segment, _), (address, size) in sections.items():
        if not segment.startswith('__DATA'):
            continue
        for offset in range(0, size-16, 8):
            try:
                if string_at(cache.read, cache.pointer(address+offset), text) != 'host_info':
                    continue
                if string_at(cache.read, cache.pointer(address+offset+16), text) is None:
                    continue
            except KeyError:
                continue
            found.append(address+offset)
    if len(found) != 1:
        raise ValueError(f'expected one kernel-mig-routine table in {image}, found {len(found)}')
    raw = lambda a: struct.unpack('<Q', cache.read(a, 8))[0]
    return dict(address=hex(found[0]), entries=mig_entries(cache.pointer, raw, cache.read, found[0], text))


def donor_mig(kext):
    """The donor kext's kernel-mig-routine table (_mig_routine_aliases_) and the syscall-mask filters it reads.

    profile_populate_syscall_masks asks get_entry_point_for_operation for each mask operation (movl $OP,%esi)
    and walks it for that mask's filter (movl $FILTER,%edx); each filter node's argument is a data reference to
    a bitmap object (type 1, u16 length, bytes) indexed by the donor's numbering."""
    symbols = MACHO['symbols'](kext)
    segments = MACHO['segments'](kext)
    text = (segments[b'__TEXT'][0], segments[b'__TEXT'][1])

    def read(address, size):
        for base, length, offset, _ in segments.values():
            if base <= address < base+length:
                return kext[offset+address-base:offset+address-base+size]
        raise KeyError(f'{address:#x} outside the kext')

    raw = lambda a: struct.unpack('<Q', read(a, 8))[0]
    tables = [n for n in symbols if re.fullmatch(r'__mig_routine_aliases_(\.\d+)?', n)]
    if len(tables) != 1:
        raise ValueError(f'expected one __mig_routine_aliases_ symbol, found {tables}')
    entries = mig_entries(raw, raw, read, symbols[tables[0]], text)
    ordered = sorted(symbols.values())
    start = symbols['_profile_populate_syscall_masks']
    code = read(start, next(a for a in ordered if a > start)-start)
    masks = [(struct.unpack('<I', m.group(1))[0], struct.unpack('<I', m.group(2))[0])
             for m in re.finditer(rb'\xbe(....).{0,48}?\xba(....)', code, re.S)]
    if not masks:
        raise ValueError('profile_populate_syscall_masks: no operation/filter pairs found')
    return dict(symbol=tables[0], entries=entries, masks=masks)


def mig_layout(target, donor, donor_names):
    """Map each target kernel-mig-routine index to the donor's by name; None where the donor has no such routine."""
    by_name = {}
    for name, index in donor['entries']:
        by_name.setdefault(name, index)
        if by_name[name] != index:
            raise ValueError(f'{name}: two donor indices')
    count = max(i for _, i in target['entries'])+1
    names = [[] for _ in range(count)]
    for name, index in target['entries']:
        names[index].append(name)
    mapping, dropped = [], []
    for index, aliases in enumerate(names):
        found = {by_name[n] for n in aliases if n in by_name}
        if len(found) > 1:
            raise ValueError(f'target routine {index} {aliases}: donor indices {sorted(found)}')
        mapping.append(min(found) if found else None)
        if not found:
            dropped.append(dict(index=index, names=aliases))
    used = [d for d in mapping if d is not None]
    if len(used) != len(set(used)):
        raise ValueError('two target routines map to one donor routine')
    mig = [(op, f) for op, f in donor['masks'] if op < len(donor_names) and donor_names[op] == 'syscall-mig']
    if len(mig) != 1:
        raise ValueError(f'expected one syscall-mig mask in the donor, found {donor["masks"]}')
    donor_names_by_index = {}
    for name, index in donor['entries']:
        donor_names_by_index.setdefault(index, name)
    return dict(filter=mig[0][1], donor_operation=mig[0][0], target_count=count,
                target_names=[a[0] if a else None for a in names],
                donor_names=[donor_names_by_index.get(i) for i in range(max(donor_names_by_index)+1)],
                donor_count=max(i for _, i in donor['entries'])+1, map=mapping, dropped=dropped,
                target_address=target['address'], donor_symbol=donor['symbol'],
                masks=[dict(operation=donor_names[op], donor_operation=op, filter=f) for op, f in donor['masks']])


def op_layout(target, donor, donor_count):
    """Target indices without a donor entry; all other names must match in order."""
    position = {n: i for i, n in enumerate(donor)}
    if len(position) != len(donor):
        raise ValueError('donor operation names repeat')
    mapping, drop, last = [], [], -1
    for index, name in enumerate(target):
        donor_index = position.get(name)
        if donor_index is None:
            drop.append(index)
        elif donor_index != last+1:
            raise ValueError(f'{name}: donor index {donor_index} breaks the order (expected {last+1})')
        else:
            last = donor_index
        mapping.append(donor_index)
    if last != len(donor)-1:
        raise ValueError(f'donor names after {donor[last+1] if last+1 < len(donor) else None} are missing in the target')
    target_count = target.index(FIRST_MESSAGE_FILTER)
    if donor.index(FIRST_MESSAGE_FILTER) != donor_count:
        raise ValueError(f'donor operation count {donor_count} does not end at {FIRST_MESSAGE_FILTER}')
    if any(i >= target_count for i in drop):
        raise ValueError('a message-filter operation has no donor entry')
    return dict(target_operation_count=target_count, donor_operation_count=donor_count,
                target_name_count=len(target), donor_name_count=len(donor), drop=drop, map=mapping)


def boot_kc_copy(kc, uuid, offset, prefix):
    """The donor's copy in a boot collection: same UUID and the same entry bytes at the same __TEXT offset."""
    data = kc.read_bytes()
    for cmd, p in MACHO['commands'](data):
        if cmd != 0x80000035:
            continue
        header_address, header_offset = struct.unpack_from('<QQ', data, p+8)
        if next((data[q+8:q+24].hex() for c, q in MACHO['commands'](data, header_offset) if c == 0x1b), None) != uuid:
            continue
        entry = MACHO['segments'](data, header_offset)[b'__TEXT']
        start = entry[2]+int(offset, 0)
        if data[start:start+len(prefix)//2] != bytes.fromhex(prefix):
            raise ValueError('the boot collection copy of the donor has other profile_init entry bytes')
        name = data[p+struct.unpack_from('<I', data, p+24)[0]:].split(b'\0', 1)[0].decode()
        return dict(sha256=hashlib.sha256(data).hexdigest(), entry=name, header=hex(header_address))
    raise ValueError('the boot collection has no copy of the donor kext')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dyld-cache', type=Path, required=True,
                        help='main file of the target x86_64 dyld shared cache (subcaches next to it)')
    parser.add_argument('--image', default='/usr/lib/libsandbox.1.dylib')
    parser.add_argument('--sandbox-kext', type=Path, required=True, help='donor Sandbox x86_64 kext binary')
    parser.add_argument('--boot-kc', type=Path, help='boot collection holding the copy of the donor that runs')
    parser.add_argument('--out', type=Path, required=True, help='new JSON file')
    args = parser.parse_args()
    if args.out.exists():
        raise SystemExit(f'{args.out} exists')
    cache = Cache(args.dyld_cache)
    target = target_names(cache, args.image)
    kext = args.sandbox_kext.read_bytes()
    donor = donor_identity(kext)
    layout = op_layout(target['names'], donor['names'], donor['operation_count'])
    layout['mig'] = mig_layout(target_mig(cache, args.image), donor_mig(kext), donor['names'])
    git = lambda *a: subprocess.check_output(['git', '-C', str(HERE), *a], text=True).strip()
    revision = runpy.run_path(str(HERE/'unit.py'))['REVISION']
    record = dict(unit='tools/kernel-foundation', revision=revision, operation='sandbox-layout',
        date=datetime.datetime.now(datetime.timezone.utc).isoformat(), commit=git('rev-parse', 'HEAD'),
        dirty=bool(git('status', '--porcelain')),
        target=dict(cache=str(args.dyld_cache.resolve()), cache_uuid=cache.uuid, cache_sha256=digest(args.dyld_cache),
                    **target),
        donor_sha256=hashlib.sha256(kext).hexdigest(), donor=dict(path=str(args.sandbox_kext.resolve()), **donor),
        **layout)
    if args.boot_kc:
        record['boot_kc'] = boot_kc_copy(args.boot_kc, donor['uuid'], donor['profile_init']['offset'],
                                         donor['profile_init']['prefix_hex'])
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, 'x') as f:
        f.write(json.dumps(record, indent=1)+'\n')
    print(args.out)


if __name__ == '__main__':
    main()
