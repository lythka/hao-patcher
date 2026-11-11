"""check.py: verify a dylib cache_dylib.py extracted, with the host's tools.

    python3.14 -I check.py DYLIB REPORT_JSON

Every RIP-relative operand of the disassembled code (llvm-objdump) must land inside one of the dylib's segments, and
every one landing in the private GOT segment (__CACHE_GOT) on a slot the dylib binds or rebases (dyld_info -fixups).
A target outside every segment is an error when it lies in the source cache's uniqued GOT region (a GOT slot the
extraction missed); elsewhere it is a decoding artifact of data inside __text (absurd displacements such as
0x3fffffff) and is listed, not failed. Prints a JSON record; exit status 1 on errors.
"""
import json
from pathlib import Path
import re
import subprocess
import sys

OPERAND = re.compile(r'^\s*([0-9a-f]+):\s+(\S+)\s.*\(%rip\).*## (0x[0-9a-f]+)')
FIXUP = re.compile(r'^\s+\S+\s+\S+\s+(0x[0-9A-Fa-f]+)\s+(bind|rebase)\b')


def run(*args):
    return subprocess.run(list(map(str, args)), check=True, capture_output=True, text=True).stdout


def segments(path):
    found = []
    for block in run('otool', '-l', path).split('Load command')[1:]:
        if 'cmd LC_SEGMENT_64' not in block:
            continue
        field = dict(re.findall(r'^\s*(segname|vmaddr|vmsize)\s+(\S+)', block, re.M))
        found.append((field['segname'], int(field['vmaddr'], 16), int(field['vmaddr'], 16) + int(field['vmsize'], 16)))
    return found


def fixups(path):
    return {int(m[1], 16): m[2] for m in map(FIXUP.match, run('xcrun', 'dyld_info', '-fixups', path).splitlines()) if m}


class Image:
    """The dylib's bytes by address (file-backed segments only)."""

    def __init__(self, path):
        self.data = Path(path).read_bytes()
        self.segs, self.sects = [], {}
        for block in run('otool', '-l', path).split('Load command')[1:]:
            if 'cmd LC_SEGMENT_64' in block:
                f = dict(re.findall(r'^\s*(segname|vmaddr|vmsize|fileoff|filesize)\s+(\S+)', block, re.M))
                self.segs.append((f['segname'], int(f['vmaddr'], 16), int(f['vmsize'], 16), int(f['fileoff']),
                                  int(f['filesize'])))
                for sect in block.split('Section')[1:]:
                    g = dict(re.findall(r'^\s*(sectname|addr|size)\s+(\S+)', sect, re.M))
                    self.sects[g['sectname']] = (int(g['addr'], 16), int(g['size'], 16))

    def mapped(self, address):
        return any(vm <= address < vm + size for name, vm, size, _, _ in self.segs if name != '__LINKEDIT')

    def read(self, address, size):
        for name, vm, vmsize, off, filesize in self.segs:
            if vm <= address and address + size <= vm + filesize:
                return self.data[off + address - vm:off + address - vm + size]
        raise LookupError(f'{address:#x}')

    def i32(self, address):
        return int.from_bytes(self.read(address, 4), 'little', signed=True)


def swift(image, fixed):
    """Relative pointers of the Swift metadata the runtime scans: conformance records (protocol, type, witness
    table), type descriptors (parent, name), field descriptors and every symbolic reference in the mangled names of
    __swift5_typeref/__swift5_fieldmd/__swift5_assocty. Each must land inside the dylib; an indirect one on a slot the
    dylib binds or rebases. Returns (pointers checked, [errors])."""
    errors, count = [], 0

    def relative(at, indirectable=False, what=''):
        nonlocal count
        count += 1
        offset = image.i32(at)
        if offset == 0:
            return None
        target = at + (offset & ~1 if indirectable else offset)
        if not image.mapped(target):
            errors.append(f'{what} at {at:#x} -> {target:#x} outside the dylib')
            return None
        if indirectable and offset & 1:
            if target not in fixed and int.from_bytes(image.read(target, 8), 'little') == 0:
                errors.append(f'{what} at {at:#x}: indirect slot {target:#x} is neither bound nor rebased')
            return None
        return target

    def symbolic(start, end):
        at = start
        while at < end:
            byte = image.read(at, 1)[0]
            if byte == 0:
                at += 1
            elif byte <= 0x17:
                relative(at + 1, False, f'symbolic reference {byte:#x}') if byte not in (0x02, 0x0A) else \
                    relative_indirect(at + 1)
                at += 5
            elif byte <= 0x1F:
                at += 9
            else:
                at += 1

    def relative_indirect(at):
        nonlocal count
        count += 1
        target = at + image.i32(at)
        if not image.mapped(target):
            errors.append(f'indirect symbolic reference at {at:#x} -> {target:#x} outside the dylib')
        elif target not in fixed and int.from_bytes(image.read(target, 8), 'little') == 0:
            errors.append(f'indirect symbolic reference at {at:#x}: slot {target:#x} unbound')

    addr, size = image.sects.get('__swift5_proto', (0, 0))
    for at in range(addr, addr + size, 4):
        record = relative(at, what='conformance record')
        if record is None:
            continue
        relative(record, True, 'conformance protocol')
        flags = int.from_bytes(image.read(record + 12, 4), 'little')
        kind = (flags >> 3) & 7
        if kind in (0, 1):  # direct or indirect type descriptor
            relative_indirect(record + 4) if kind == 1 else relative(record + 4, what='conformance type')
        relative(record + 8, what='witness table pattern')
    addr, size = image.sects.get('__swift5_types', (0, 0))
    for at in range(addr, addr + size, 4):
        offset = image.i32(at)
        target = at + (offset & ~3)
        count += 1
        if not image.mapped(target):
            errors.append(f'type record at {at:#x} -> {target:#x} outside the dylib')
            continue
        if offset & 3:
            if target not in fixed:
                errors.append(f'type record at {at:#x}: indirect slot unbound')
            continue
        relative(target + 4, True, 'type parent')
        relative(target + 8, what='type name')
    for name in ('__swift5_typeref', '__swift5_assocty'):
        if name in image.sects:
            start, length = image.sects[name]
            if name == '__swift5_typeref':
                symbolic(start, start + length)
    addr, size = image.sects.get('__swift5_fieldmd', (0, 0))
    at = addr
    while at < addr + size:
        relative(at, what='field descriptor type name')
        relative(at + 4, what='field descriptor superclass')
        record_size, count_fields = int.from_bytes(image.read(at + 10, 2), 'little'), \
            int.from_bytes(image.read(at + 12, 4), 'little')
        for i in range(count_fields):
            field = at + 16 + i*record_size
            relative(field + 4, what='field type name')
            relative(field + 8, what='field name')
        at += 16 + count_fields*record_size
    return count, errors


def check(path, report):
    segs = segments(path)
    fixed = fixups(path)
    region = [int(x, 16) for x in report['stats'].get('uniqued_got_region', ['0x0', '0x0'])]
    got = next(((lo, hi) for name, lo, hi in segs if name == '__CACHE_GOT'), (0, 0))
    total = 0
    used, unbound, missed, artifacts = set(), set(), [], []
    for line in run('xcrun', 'llvm-objdump', '-d', '--no-show-raw-insn', path).splitlines():
        m = OPERAND.match(line)
        if not m:
            continue
        total += 1
        target = int(m[3], 16)
        if got[0] <= target < got[1]:
            used.add(target)
            if target not in fixed:
                unbound.add(target)
        elif not any(lo <= target < hi for name, lo, hi in segs if name != '__LINKEDIT'):
            (missed if region[0] <= target < region[1] else artifacts).append(line.strip())
    errors = [f'GOT slot {t:#x} used by code is neither bound nor rebased' for t in sorted(unbound)]
    errors += [f'code uses a uniqued GOT slot outside the dylib: {line}' for line in missed]
    swift_count, swift_errors = swift(Image(path), fixed)
    return dict(rip_operands=total, got_slots_used=len(used), fixups=len(fixed), swift_relative_pointers=swift_count,
                decoding_artifacts=artifacts, errors=errors + swift_errors)


def main():
    path, report = sys.argv[1], json.loads(Path(sys.argv[2]).read_text())
    record = check(path, report)
    print(json.dumps(record, indent=2))
    sys.exit(1 if record['errors'] else 0)


if __name__ == '__main__':
    main()
