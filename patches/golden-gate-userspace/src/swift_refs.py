"""swift_refs.py: check the Swift metadata references of every image in a dyld shared cache family.

For each image it walks __swift5_types, __swift5_proto and __swift5_protos, follows every relative pointer
(direct and indirect) and every type and protocol descriptor's name, and reports targets that lie outside the
mapped ranges of the cache family or names that are not NUL-terminated inside them. The Swift runtime follows the
same pointers when it searches conformances by mangled name, in every process that loads the image.

    python3.14 -I swift_refs.py CACHE_DIRECTORY [--arch x86_64] [--limit N]
    python3.14 -I swift_refs.py CACHE_DIRECTORY --plan [--reference ARM64E_CACHE_DIRECTORY]

Exit status 1 when a bad reference is found. --plan prints the x86_64 repairs (plan_cache_family).
"""
import argparse
from pathlib import Path
import struct
import sys

MH_MAGIC_64 = 0xFEEDFACF
LC_SEGMENT_64 = 0x19
SECTIONS = (b'__swift5_types', b'__swift5_proto', b'__swift5_protos')


class Family:
    """The mappings of one cache family (main cache and its subcaches), read through open files."""

    def __init__(self, directory, arch='x86_64'):
        stem = f'dyld_shared_cache_{arch}'
        paths = sorted(p for p in Path(directory).glob(stem + '*')
                       if p.suffix not in ('.map', '.atlas', '.symbols') and not p.is_symlink())
        if not paths or paths[0].name != stem:
            raise FileNotFoundError(f'{directory}: no {stem}')
        self.files, self.mappings, self.slide = {}, [], {}
        for path in paths:
            stream = path.open('rb')
            self.files[path.name] = stream
            head = stream.read(0x200)
            if not head.startswith(b'dyld_v1'):
                raise ValueError(f'{path}: not a dyld shared cache')
            offset, count = struct.unpack_from('<II', head, 0x138)
            stream.seek(offset)
            table = stream.read(count*56)
            for i in range(count):
                address, size, file_offset, slide_offset, slide_size, _, _, _ = \
                    struct.unpack_from('<QQQQQQII', table, i*56)
                self.mappings.append((address, address+size, path.name, file_offset))
                if slide_size:
                    stream.seek(slide_offset)
                    info = stream.read(40)
                    version, = struct.unpack_from('<I', info)
                    if version == 2:
                        mask, add = struct.unpack_from('<QQ', info, 24)
                    elif version in (3, 5):
                        mask, add = None, struct.unpack_from('<Q', info, 16)[0]
                    else:
                        raise ValueError(f'{path}: slide info v{version} not supported')
                    self.slide[(path.name, address)] = (version, mask, add)
        self.mappings.sort()
        main = self.files[stem]
        main.seek(0)
        head = main.read(0x200)
        offset, count = struct.unpack_from('<II', head, 0x1c0)
        main.seek(offset)
        table = main.read(count*32)
        self.images = []
        for i in range(count):
            address, _, _, name = struct.unpack_from('<QQQI', table, i*32)
            main.seek(name)
            self.images.append((address, main.read(1024).split(b'\0', 1)[0].decode()))

    def locate(self, address):
        for start, end, name, file_offset in self.mappings:
            if start <= address < end:
                return name, file_offset+address-start, end, start
        return None

    def mapped(self, address, size=1):
        where = self.locate(address)
        return where is not None and address+size <= where[2]

    def read(self, address, size):
        where = self.locate(address)
        if where is None or address+size > where[2]:
            raise LookupError(hex(address))
        stream = self.files[where[0]]
        stream.seek(where[1])
        return stream.read(size)

    def pointer(self, address):
        """The unslid target of the 8-byte pointer at ADDRESS (slide info v2, v3 or v5), or None if unmapped."""
        where = self.locate(address)
        if where is None or address+8 > where[2]:
            return None
        raw, = struct.unpack('<Q', self.read(address, 8))
        version, mask, add = self.slide.get((where[0], where[3]), (2, 0, 0))
        if raw == 0:
            return 0
        if version == 2:
            value = raw & ~mask & 0xFFFFFFFFFFFFFFFF
            return value+add if value else 0
        if version == 3:
            if raw >> 63:
                return add+(raw & 0xFFFFFFFF)
            return (raw & 0x7FFFFFFFFFF) | ((raw >> 43) & 0xFF) << 56
        return add+(raw & 0x3FFFFFFFF)  # v5: runtime offset from the cache base

    def cstring(self, address):
        """True when ADDRESS starts a NUL-terminated string within one mapping."""
        where = self.locate(address)
        if where is None:
            return False
        size = min(4096, where[2]-address)
        return b'\0' in self.read(address, size)

    def close(self):
        for stream in self.files.values():
            stream.close()


def sections(family, header):
    magic, _, _, _, ncmds, sizeofcmds = struct.unpack('<IiiIII', family.read(header, 24))
    if magic != MH_MAGIC_64:
        raise ValueError(f'{header:#x}: not a 64-bit Mach-O')
    commands = family.read(header+32, sizeofcmds)
    at = 0
    for _ in range(ncmds):
        cmd, size = struct.unpack_from('<II', commands, at)
        if cmd == LC_SEGMENT_64:
            nsects, = struct.unpack_from('<I', commands, at+64)
            for i in range(nsects):
                sect = at+72+i*80
                name = commands[sect:sect+16].rstrip(b'\0')
                segment = commands[sect+16:sect+32].rstrip(b'\0')
                address, length = struct.unpack_from('<QQ', commands, sect+32)
                if segment == b'__TEXT' and name in SECTIONS:
                    yield name.decode(), address, length
        at += size


def relative(family, address, indirectable=False):
    """Resolve a 32-bit relative pointer at ADDRESS: (target, error or None)."""
    offset, = struct.unpack('<i', family.read(address, 4))
    indirect = indirectable and offset & 1
    target = address+(offset & ~1 if indirectable else offset)
    if not family.mapped(target, 8 if indirect else 1):
        return target, f'target {target:#x} unmapped'
    if indirect:
        value = family.pointer(target)
        if value == 0:
            return 0, None              # weak reference, resolved at run time
        if not family.mapped(value, 1):
            return value, f'indirect {target:#x} -> {value:#x} unmapped'
        return value, None
    return target, None


def check_context(family, descriptor, issues, where):
    """A type or protocol context descriptor: flags, parent, name."""
    if not family.mapped(descriptor, 12):
        issues.append(f'{where}: descriptor {descriptor:#x} unmapped')
        return
    flags, = struct.unpack('<I', family.read(descriptor, 4))
    kind = flags & 0x1F
    if kind not in (3, 4, 16, 17, 18):  # protocol, opaque type, class, struct, enum
        issues.append(f'{where}: descriptor {descriptor:#x} has kind {kind} (flags {flags:#x})')
        return
    if kind == 4:
        return                          # opaque type descriptors have no name
    name, error = relative(family, descriptor+8)
    if error:
        issues.append(f'{where}: descriptor {descriptor:#x} name {error}')
    elif not family.cstring(name):
        issues.append(f'{where}: descriptor {descriptor:#x} name {name:#x} not terminated in its mapping')


def check_image(family, header, path):
    issues = []
    counts = dict(types=0, proto=0, protos=0)
    for section, address, length in sections(family, header):
        if not family.mapped(address, max(length, 1)):
            issues.append(f'{path}: {section} {address:#x}+{length:#x} unmapped')
            continue
        for entry in range(address, address+length - length % 4, 4):
            where = f'{path} {section}[{(entry-address)//4}]'
            if section == '__swift5_types':
                counts['types'] += 1
                raw, = struct.unpack('<i', family.read(entry, 4))
                kind = raw & 3
                target = entry+(raw & ~3)
                if kind == 1:
                    if not family.mapped(target, 8):
                        issues.append(f'{where}: indirect slot {target:#x} unmapped')
                        continue
                    target = family.pointer(target)
                    if target == 0:
                        continue
                if kind > 1:
                    issues.append(f'{where}: reference kind {kind}')
                    continue
                check_context(family, target, issues, where)
            elif section == '__swift5_protos':
                counts['protos'] += 1
                target, error = relative(family, entry, indirectable=True)
                if error:
                    issues.append(f'{where}: {error}')
                elif target:
                    check_context(family, target, issues, where)
            else:
                counts['proto'] += 1
                record, error = relative(family, entry, indirectable=True)
                if error:
                    issues.append(f'{where}: {error}')
                    continue
                if not family.mapped(record, 16):
                    issues.append(f'{where}: conformance {record:#x} unmapped')
                    continue
                flags, = struct.unpack('<I', family.read(record+12, 4))
                protocol, error = relative(family, record, indirectable=True)
                if error:
                    issues.append(f'{where}: conformance {record:#x} protocol {error}')
                elif protocol:
                    check_context(family, protocol, issues, f'{where} protocol')
                table, = struct.unpack('<i', family.read(record+8, 4))
                if table:
                    slot = record+8+table
                    if not family.mapped(slot, 8):
                        issues.append(f'{where}: conformance {record:#x} witness table {slot:#x} unmapped')
                    elif family.pointer(slot) != record:
                        issues.append(f'{where}: conformance {record:#x} witness table {slot:#x} '
                                      f'names {family.pointer(slot):#x}')
                kind = (flags >> 3) & 7
                target, error = relative(family, record+4)
                if error:
                    issues.append(f'{where}: conformance {record:#x} type {error}')
                    continue
                if kind == 0:
                    check_context(family, target, issues, f'{where} type')
                elif kind == 1:
                    if not family.mapped(target, 8):
                        issues.append(f'{where}: conformance {record:#x} type slot {target:#x} unmapped')
                        continue
                    value = family.pointer(target)
                    if value:
                        check_context(family, value, issues, f'{where} type')
                elif kind == 2:
                    if not family.cstring(target):
                        issues.append(f'{where}: conformance {record:#x} ObjC name {target:#x} bad')
                elif kind == 3:
                    if not family.mapped(target, 8):
                        issues.append(f'{where}: conformance {record:#x} ObjC class slot {target:#x} unmapped')
                else:
                    issues.append(f'{where}: conformance {record:#x} type reference kind {kind}')
    return counts, issues


def context_path(family, descriptor):
    """(kind, name) of a context descriptor and each parent, innermost first; names only where the kind has one."""
    path = []
    seen = set()
    while descriptor and descriptor not in seen and len(path) < 32:
        seen.add(descriptor)
        if not family.mapped(descriptor, 12):
            return None
        flags, = struct.unpack('<I', family.read(descriptor, 4))
        kind = flags & 0x1F
        name = None
        if kind in (0, 3, 16, 17, 18):
            target, error = relative(family, descriptor+8)
            if error or not family.cstring(target):
                return None
            name = family.read(target, min(4096, family.locate(target)[2]-target)).split(b'\0', 1)[0]
        path.append((kind, name))
        if kind == 0:
            return tuple(path)
        descriptor, error = relative(family, descriptor+4, indirectable=True)
        if error:
            return None
    return None


def image_sections(family, header):
    return {name: (address, length) for name, address, length in sections(family, header)}


def conformances(family, header):
    """[(record address, flags)] of an image's __swift5_proto, in section order."""
    address, length = image_sections(family, header).get('__swift5_proto', (0, 0))
    result = []
    for entry in range(address, address+length - length % 4, 4):
        record, error = relative(family, entry, indirectable=True)
        if error or not family.mapped(record, 16):
            raise ValueError(f'{entry:#x}: conformance record unreadable')
        result.append((record, struct.unpack('<I', family.read(record+12, 4))[0]))
    return result


def conformance_type(family, record, flags):
    """The type descriptor a direct or indirect conformance names, or None for ObjC class references."""
    kind = (flags >> 3) & 7
    target, _ = relative(family, record+4)
    if kind == 0:
        return target
    if kind == 1:
        return family.pointer(target) if family.mapped(target, 8) else None
    return None


def type_descriptors(family, header):
    """{context path: [descriptor]} of the image's __swift5_types."""
    address, length = image_sections(family, header).get('__swift5_types', (0, 0))
    result = {}
    for entry in range(address, address+length - length % 4, 4):
        raw, = struct.unpack('<i', family.read(entry, 4))
        target = entry+(raw & ~3)
        if raw & 3 == 1:
            target = family.pointer(target) if family.mapped(target, 8) else None
        elif raw & 3:
            continue
        path = context_path(family, target) if target else None
        if path:
            result.setdefault(path, []).append(target)
    return result


def plan_cache_family(directory, reference=None):
    """File-offset edits that point broken x86_64 conformance type references at their descriptors.

    Apple's x86_64 cache (RosettaOS) leaves some direct conformance type references of a few images unadjusted:
    they point at a fixed place before the records that now holds other bytes (26A428: 21 references, 26B131:
    47). The arm64e cache of the same build (in REFERENCE, by default the same directory) names the intended type
    of every conformance; the repair looks that type up by its full context path among the image's own x86_64
    descriptors and refuses any reference it cannot match exactly once."""
    x86 = Family(directory, 'x86_64')
    try:
        arm = Family(reference or directory, 'arm64e')
    except BaseException:
        x86.close()
        raise
    try:
        arm_images = {path: header for header, path in arm.images}
        plan = {}
        for header, image in x86.images:
            records = conformances(x86, header)
            broken = []
            for index, (record, flags) in enumerate(records):
                if (flags >> 3) & 7:
                    continue
                issues = []
                check_context(x86, conformance_type(x86, record, flags), issues, image)
                if issues:
                    broken.append(index)
            if not broken:
                continue
            if image not in arm_images:
                raise ValueError(f'MISSING/unsupported: {image} has no arm64e counterpart')
            reference_records = conformances(arm, arm_images[image])
            if len(reference_records) != len(records):
                raise ValueError(f'MISSING/unsupported: {image} conformance counts differ '
                                 f'({len(records)} x86_64, {len(reference_records)} arm64e)')
            candidates = type_descriptors(x86, header)
            for index in broken:
                record, flags = records[index]
                arm_record, arm_flags = reference_records[index]
                protocol = context_path(x86, relative(x86, record, indirectable=True)[0])
                arm_protocol = context_path(arm, relative(arm, arm_record, indirectable=True)[0])
                if arm_flags != flags or protocol is None or protocol != arm_protocol:
                    raise ValueError(f'MISSING/unsupported: {image} conformance {index} differs from arm64e')
                wanted = context_path(arm, conformance_type(arm, arm_record, arm_flags))
                matches = candidates.get(wanted, []) if wanted else []
                if len(matches) != 1:
                    raise ValueError(f'MISSING/unsupported: {image} conformance {index}: '
                                     f'{len(matches)} x86_64 descriptors match {wanted}')
                field = record+4
                name, offset, _, _ = x86.locate(field)
                type_name = b'.'.join(n for _, n in reversed(wanted) if n).decode()
                protocol_name = b'.'.join(n for _, n in reversed(protocol) if n).decode()
                plan.setdefault(Path(directory)/name, []).append(
                    (offset, x86.read(field, 4), struct.pack('<i', matches[0]-field),
                     f'{image.rsplit("/", 1)[-1]} {type_name}: {protocol_name} type reference'))
        return plan
    finally:
        x86.close()
        arm.close()


def verify(directory, arch='x86_64'):
    """(image count, totals, [issues]) for one cache family."""
    family = Family(directory, arch)
    try:
        totals = dict(types=0, proto=0, protos=0)
        issues = []
        for header, path in family.images:
            counts, found = check_image(family, header, path)
            for key, value in counts.items():
                totals[key] += value
            issues.extend(found)
        return len(family.images), totals, issues
    finally:
        family.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('directory', type=Path)
    parser.add_argument('--arch', default='x86_64')
    parser.add_argument('--limit', type=int, default=50, help='issues to print (default 50)')
    parser.add_argument('--plan', action='store_true',
                        help='print the x86_64 conformance repairs, using the arm64e cache (--reference)')
    parser.add_argument('--reference', type=Path, help='arm64e cache directory (default: DIRECTORY)')
    args = parser.parse_args(argv)
    if args.plan:
        for path, edits in plan_cache_family(args.directory, args.reference).items():
            for offset, before, after, reason in edits:
                print(f'{path.name} {offset:#x} {before.hex()} -> {after.hex()} {reason}')
        return 0
    images, totals, issues = verify(args.directory, args.arch)
    for line in issues[:args.limit]:
        print(line)
    print(f'{images} images; {totals["types"]} types, {totals["proto"]} conformances, '
          f'{totals["protos"]} protocols; {len(issues)} bad references')
    return 1 if issues else 0


if __name__ == '__main__':
    sys.exit(main())
