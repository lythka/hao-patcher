"""Port a kernel-fixes profile to another Darwin 27 x86_64 kernel.

Every field of the source profile is derived again from the new kernel: by symbol, by a masked code signature
(RIP-relative displacements and rel32 branch targets masked, named symbols they reference required to agree) or by
a same-code check of the function the field relies on. The source kernel is checked the same way first, so a
recipe that does not reproduce the source profile stops too. Anything that cannot be verified stops the port with
`MISSING: <item>`; nothing is guessed.

Definitions that hold Boot kernel collection addresses (early-vm, vm-test-layout, iosurface-layout) need the new
kernel's Boot KC; without one they are left PENDING and `kc-recipe.json` holds their standalone-kernel positions.
"""
import bisect
from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
import re
import shutil
import struct
import subprocess

LC_SEGMENT_64, LC_SYMTAB, LC_UUID, LC_FILESET_ENTRY = 0x19, 0x2, 0x1b, 0x80000035
KERNEL_ENTRIES = (b'com.apple.kernel', b'mach_kernel')


class Missing(ValueError):
    """A field of the profile that the new inputs do not support."""

    def __init__(self, item, reason):
        super().__init__(f'MISSING: {item}: {reason}')
        self.item = item


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def commands(data, offset=0):
    if struct.unpack_from('<II', data, offset) != (0xfeedfacf, 0x1000007):
        raise ValueError('expected a thin x86_64 Mach-O')
    pos = offset+32
    for _ in range(struct.unpack_from('<I', data, offset+16)[0]):
        cmd, size = struct.unpack_from('<II', data, pos)
        yield cmd, pos
        pos += size


def segments(data, offset=0):
    """{name: (vmaddr, vmsize, fileoff, filesize)}"""
    return {data[p+8:p+24].split(b'\0')[0].decode(): struct.unpack_from('<QQQQ', data, p+24)
            for cmd, p in commands(data, offset) if cmd == LC_SEGMENT_64}


def sections(data, offset=0):
    """{(segment, section): (addr, size, fileoff)}"""
    result = {}
    for cmd, p in commands(data, offset):
        if cmd == LC_SEGMENT_64:
            segment = data[p+8:p+24].split(b'\0')[0].decode()
            for k in range(struct.unpack_from('<I', data, p+64)[0]):
                q = p+72+80*k
                addr, size, fileoff = struct.unpack_from('<QQI', data, q+32)
                result[(segment, data[q:q+16].split(b'\0')[0].decode())] = (addr, size, fileoff)
    return result


def uuid_of(data, offset=0):
    return next((data[p+8:p+24].hex() for cmd, p in commands(data, offset) if cmd == LC_UUID), None)


class Kernel:
    """A standalone thin x86_64 kernel: bytes, identity, segments and its defined (global) symbols."""

    def __init__(self, path):
        self.path = Path(path)
        self.data = self.path.read_bytes()
        self.sha256 = sha256(self.data)
        self.uuid = uuid_of(self.data)
        self.segments = segments(self.data)
        self.text = sections(self.data)[('__TEXT', '__text')]
        version = re.search(rb'Darwin Kernel Version ([0-9.]+):[^\0]*?(xnu-[0-9.]+)', self.data)
        self.darwin, self.xnu = (version.group(1).decode(), version.group(2).decode()) if version else (None, None)
        self.symbols = {}
        p = next(p for cmd, p in commands(self.data) if cmd == LC_SYMTAB)
        off, count, strings, _ = struct.unpack_from('<4I', self.data, p+8)
        for i in range(count):
            name, kind, sect, _, address = struct.unpack_from('<IBBHQ', self.data, off+i*16)
            if name and kind & 0xe == 0xe and sect:
                self.symbols.setdefault(self.data[strings+name:].split(b'\0', 1)[0].decode(), address)
        self.by_address = {}
        for name, address in sorted(self.symbols.items()):
            self.by_address.setdefault(address, name)
        self.addresses = sorted(self.by_address)

    def segment_of(self, va):
        for name, (base, size, _, _) in self.segments.items():
            if base <= va < base+size and size:
                return name, va-base
        raise ValueError(f'{va:#x} is outside the kernel segments')

    def fileoff(self, va):
        for base, size, off, filesize in self.segments.values():
            if base <= va < base+filesize:
                return off+va-base
        raise ValueError(f'{va:#x} is not backed by kernel file bytes')

    def read(self, va, size):
        start = self.fileoff(va)
        return self.data[start:start+size]

    def address(self, name):
        if name not in self.symbols:
            raise Missing(name, f'symbol not in kernel {self.uuid}')
        return self.symbols[name]

    def function(self, name):
        """(start, end) of a named function: up to the next symbol, within __text."""
        start = self.address(name)
        text_end = self.text[0]+self.text[1]
        if not self.text[0] <= start < text_end:
            raise Missing(name, 'not a __text function')
        i = bisect.bisect_right(self.addresses, start)
        return start, min(self.addresses[i] if i < len(self.addresses) else text_end, text_end)

    def enclosing(self, va):
        """The nearest symbol at or before va inside __text (a decoding start, not necessarily va's function)."""
        i = bisect.bisect_right(self.addresses, va)-1
        while i >= 0 and not self.text[0] <= self.addresses[i] <= va:
            i -= 1
        if i < 0:
            raise ValueError(f'{va:#x} has no symbol before it in __text')
        return self.addresses[i], self.by_address[self.addresses[i]]

    def name_at(self, va):
        return self.by_address.get(va)


class Collection:
    """A Boot kernel collection and the placement of its kernel member's segments."""

    def __init__(self, path):
        self.path = Path(path)
        self.data = self.path.read_bytes()
        self.sha256 = sha256(self.data)
        self.header = None
        for cmd, p in commands(self.data):
            if cmd == LC_FILESET_ENTRY:
                name = self.data[p+struct.unpack_from('<I', self.data, p+24)[0]:].split(b'\0', 1)[0]
                if name in KERNEL_ENTRIES:
                    self.header = struct.unpack_from('<QQ', self.data, p+8)
        if self.header is None:
            raise ValueError(f'{path}: no kernel fileset entry')
        self.kernel_segments = segments(self.data, self.header[1])
        self.kernel_uuid = uuid_of(self.data, self.header[1])

    def check(self, kernel):
        if self.kernel_uuid != kernel.uuid:
            raise Missing('Boot KC', f'its kernel UUID {self.kernel_uuid} is not kernel {kernel.uuid}')

    def from_kernel(self, kernel, va):
        name, offset = kernel.segment_of(va)
        return self.kernel_segments[name][0]+offset

    def to_kernel(self, kernel, va):
        for name, (base, size, _, _) in self.kernel_segments.items():
            if base <= va < base+size and size:
                return kernel.segments[name][0]+va-base
        raise ValueError(f'{va:#x} is outside the KC kernel segments')

    def fileoff(self, va):
        for base, size, off, filesize in segments(self.data).values():
            if base <= va < base+filesize:
                return off+va-base
        raise ValueError(f'{va:#x} is not backed by KC bytes')

    def read(self, va, size):
        start = self.fileoff(va)
        return self.data[start:start+size]


@dataclass
class Instruction:
    address: int
    raw: bytes
    text: str
    fields: list = field(default_factory=list)   # (position, 4) relocated by the linker
    refs: list = field(default_factory=list)     # absolute targets of those fields


OBJDUMP = None
LINE = re.compile(r'^\s*([0-9a-f]+):((?: [0-9a-f]{2})+) *(?:\t(.*))?$')
BRANCH = re.compile(r'^(call|jmp|j[a-z]+)\s+0x([0-9a-f]+)')


def objdump():
    global OBJDUMP
    if OBJDUMP is None:
        OBJDUMP = subprocess.run(['xcrun', '--find', 'llvm-objdump'], capture_output=True, text=True,
                                 check=True).stdout.strip()
    return OBJDUMP


def decode(kernel, start, stop):
    """Instructions of kernel from start to stop (llvm-objdump), with their relocated fields."""
    out = subprocess.run([objdump(), '-d', '--x86-asm-syntax=intel', '--no-leading-headers',
                          f'--start-address={start:#x}', f'--stop-address={stop:#x}', str(kernel.path)],
                         capture_output=True, text=True, check=True).stdout
    result = []
    for line in out.splitlines():
        m = LINE.match(line)
        if not m:
            continue
        address, raw = int(m.group(1), 16), bytes.fromhex(m.group(2).replace(' ', ''))
        if result and result[-1].address+len(result[-1].raw) == address and not m.group(3):
            result[-1].raw += raw  # a long instruction's continuation line
            continue
        text = re.sub(r'\s+', ' ', m.group(3)).strip()
        result.append(Instruction(address, raw, text))
    for ins in result:
        end = ins.address+len(ins.raw)
        rip = re.search(r'\[rip [+-] [0-9a-fx]+\].*## 0x([0-9a-f]+)', ins.text)
        branch = BRANCH.match(ins.text)
        if rip:
            target = int(rip.group(1), 16)
            disp = struct.pack('<i', target-end)
            pos = ins.raw.rfind(disp)
            if pos < 0:
                raise ValueError(f'{ins.address:#x}: RIP displacement not found in {ins.raw.hex()}')
            ins.fields.append((pos, 4)); ins.refs.append(target)
        elif branch and len(ins.raw) >= 5 and (ins.raw[-5] in (0xe8, 0xe9) or ins.raw[-6:-4][:1] == b'\x0f'):
            target = int(branch.group(2), 16)
            if struct.unpack('<i', ins.raw[-4:])[0] == target-end:
                ins.fields.append((len(ins.raw)-4, 4)); ins.refs.append(target)
    return result


def masked(instructions, start=None, stop=None):
    """(bytes, mask) of a contiguous instruction run, clipped to [start, stop)."""
    data, mask = bytearray(), bytearray()
    for ins in instructions:
        piece, keep = bytearray(ins.raw), bytearray(b'\1'*len(ins.raw))
        for pos, size in ins.fields:
            keep[pos:pos+size] = bytes(size)
        data += piece; mask += keep
    base = instructions[0].address
    lo = 0 if start is None else start-base
    hi = len(data) if stop is None else stop-base
    return bytes(data[lo:hi]), bytes(mask[lo:hi])


def pattern(data, mask):
    out, i = b'', 0
    while i < len(data):
        j = i
        while j < len(data) and mask[j] == mask[i]:
            j += 1
        out += re.escape(data[i:j]) if mask[i] else b'.{%d}' % (j-i)
        i = j
    return re.compile(out, re.S)


def matches(kernel, data, mask, region=None):
    """Addresses in kernel's __text (or region (start, stop)) where the masked bytes occur."""
    start, stop = region or (kernel.text[0], kernel.text[0]+kernel.text[1])
    off = kernel.fileoff(start)
    rx = pattern(data, mask)
    return [start+m.start()-off for m in rx.finditer(kernel.data, off, off+stop-start)]


def masked_equal(a, b, mask):
    return len(a) == len(b) and all(x == y for x, y, m in zip(a, b, mask) if m)


def names_agree(old, new, old_ins, new_ins, item):
    """Every reference to a named symbol in the old code refers to the same name in the new code; returns
    {old target: new target} for all references."""
    refs = {}
    if [i.text.split(' ', 1)[0] for i in old_ins] != [i.text.split(' ', 1)[0] for i in new_ins]:
        raise Missing(item, 'instruction sequence differs')
    for a, b in zip(old_ins, new_ins):
        for x, y in zip(a.refs, b.refs):
            if old.name_at(x) is not None and old.name_at(x) != new.name_at(y):
                raise Missing(item, f'{a.address:#x} refers to {old.name_at(x)}, the new code to {new.name_at(y) or hex(y)}')
            if refs.setdefault(x, y) != y:
                raise Missing(item, f'{x:#x} maps to two new addresses')
    return refs


NUMBER = re.compile(r'-?0x[0-9a-f]+|\b\d+\b')


def operands(ins):
    return NUMBER.sub('N', re.sub(r'<[^>]*>', '', ins.text.split('##')[0])).strip()


def differences(old, new, a, b, tolerate=None):
    """Instruction pairs that differ apart from relocated fields. Tolerated: an immediate that feeds a following
    call to _panic (a source line number), and whatever `tolerate(old_ins, new_ins)` accepts. Returns None when
    the instruction sequences do not line up."""
    if len(a) != len(b) or any(x.text.split(' ', 1)[0] != y.text.split(' ', 1)[0] for x, y in zip(a, b)):
        return None
    found, tolerated = [], []
    for k, (x, y) in enumerate(zip(a, b)):
        dx, mx = masked([x]); dy, my = masked([y])
        same = mx == my and masked_equal(dx, dy, mx)
        if same and all(old.name_at(r) == new.name_at(t) or old.name_at(r) is None for r, t in zip(x.refs, y.refs)):
            continue
        if not same and operands(x) == operands(y) and len(x.raw) == len(y.raw):
            panic = False
            for z in a[k+1:k+9]:
                m = z.text.split(' ', 1)[0]
                if m == 'call':
                    panic = any(old.name_at(r) == '_panic' for r in z.refs)
                    break
                if m.startswith('j') or m == 'ret':
                    break
            if panic:
                tolerated.append((x, y, 'panic line number')); continue
            if tolerate and tolerate(x, y):
                tolerated.append((x, y, 'immediate without effect on the profile values')); continue
        found.append((x, y))
    return found, tolerated


def same_function(old, new, name, tolerate=None):
    """(same, evidence): whether a named function has the same code in both kernels, apart from relocated fields
    and tolerated immediates."""
    a, b = decode(old, *old.function(name)), decode(new, *new.function(name))
    result = differences(old, new, a, b, tolerate)
    if result is None:
        return False, dict(function=name, old=hex(a[0].address), new=hex(b[0].address),
                           status=f'instruction sequence differs ({len(a)} -> {len(b)} instructions)')
    found, tolerated = result
    return not found, dict(function=name, old=hex(a[0].address), new=hex(b[0].address),
                           status='same code' if not found else f'{len(found)} instructions differ',
                           tolerated=[f'+{x.address-a[0].address:#x} {x.text.split("##")[0].strip()} -> '
                                      f'{y.text.split("##")[0].strip()} ({why})' for x, y, why in tolerated],
                           differences=[f'+{x.address-a[0].address:#x} {x.text} -> {y.text}' for x, y in found][:8])


def require_same(old, new, name, item, tolerate=None):
    same, evidence = same_function(old, new, name, tolerate)
    if not same:
        raise Missing(item, f'{name} changed: {evidence["status"]} {evidence.get("differences", "")}')
    return evidence


@dataclass
class Located:
    item: str
    old: int
    new: int
    window: tuple          # (start, stop) of the matched signature in the old kernel
    refs: dict             # old reference target -> new reference target


def locate(old, new, item, va, cover=0, minimum=24, before=16, after=48):
    """Find the code at old address va in the new kernel by the shortest masked window that is unique in the old
    kernel's __text, covers [va, va+cover) and is at least `minimum` bytes; it must match exactly once in the
    new kernel and every named symbol it references must agree."""
    first, _ = old.enclosing(va)
    head = decode(old, first, va+cover+after*8)
    index = next((i for i, ins in enumerate(head) if ins.address == va), None)
    if index is None:
        raise Missing(item, f'{va:#x} is not an instruction boundary when decoded from {first:#x}')
    tail = index
    while tail < len(head) and head[tail].address < va+cover:
        tail += 1
    for grow in range(before+after):
        for back in range(0, min(grow, before, index)+1):
            fwd = grow-back
            run = head[index-back:max(tail, index+1)+fwd]
            if not run or run[-1] is head[-1]:
                continue
            data, mask = masked(run)
            if len(data) < minimum:
                continue
            if len(matches(old, data, mask)) != 1:
                continue
            found = matches(new, data, mask)
            if len(found) != 1:
                raise Missing(item, f'its {len(data)}-byte signature at {run[0].address:#x} matches '
                                    f'{len(found)} times in the new kernel')
            start = found[0]
            new_run = decode(new, start, start+len(data))
            refs = names_agree(old, new, run, new_run, item)
            return Located(item, va, start+(va-run[0].address), (run[0].address, run[0].address+len(data)), refs)
    raise Missing(item, f'no unique signature around {va:#x} in the old kernel')


REG64 = {}
for _n, _names in dict(rax='eax ax al', rbx='ebx bx bl', rcx='ecx cx cl', rdx='edx dx dl', rsi='esi si sil',
                       rdi='edi di dil', rbp='ebp bp bpl', rsp='esp sp spl').items():
    for _v in [_n]+_names.split():
        REG64[_v] = _n
for _i in range(8, 16):
    for _suffix in ('', 'd', 'w', 'b'):
        REG64[f'r{_i}{_suffix}'] = f'r{_i}'
CALLER_SAVED = {'rax', 'rcx', 'rdx', 'rsi', 'rdi', 'r8', 'r9', 'r10', 'r11'}
SYMBOL_LINE = re.compile(r'^([0-9a-f]+) <(.+)>:$')


def pointer_uses(kernel, symbol):
    """{function: [(kind, displacement)]} for every slot read through the pointer variable `symbol` in __text:
    `mov R, [rip + symbol]` followed (linearly, until R is overwritten or a call clobbers it) by
    `call [R + D]` or `mov X, [R + D]`. Static functions count under the global symbol before them."""
    target = kernel.address(symbol)
    start, size = kernel.text[0], kernel.text[1]
    process = subprocess.Popen([objdump(), '-d', '--x86-asm-syntax=intel', '--no-leading-headers', '--no-show-raw-insn',
                                f'--start-address={start:#x}', f'--stop-address={start+size:#x}', str(kernel.path)],
                               stdout=subprocess.PIPE, text=True)
    uses, function, held = {}, None, set()
    for line in process.stdout:
        m = SYMBOL_LINE.match(line.strip())
        if m:
            function, held = m.group(2), set()
            continue
        parts = line.rstrip('\n').split('\t')
        if len(parts) < 2:
            continue
        mnemonic = parts[1].strip()
        args = parts[2].split('##')[0].strip() if len(parts) > 2 else ''
        comment = parts[2].split('##', 1)[1] if len(parts) > 2 and '##' in parts[2] else ''
        first = REG64.get(args.split(',')[0].strip()) if args else None
        load = re.match(r'(r[a-z0-9]+), qword ptr \[rip [+-] 0x[0-9a-f]+\]$', args)
        if mnemonic == 'mov' and load and comment and int(comment.split()[0], 16) == target:
            held.add(REG64[load.group(1)])
            continue
        slot = re.search(r'qword ptr \[(r[a-z0-9]+) \+ (0x[0-9a-f]+)\]', args)
        if slot and slot.group(1) in held and mnemonic in ('call', 'mov'):
            uses.setdefault(function, []).append(('call' if mnemonic == 'call' else 'load', int(slot.group(2), 16)))
        if mnemonic == 'call':
            held -= CALLER_SAVED
        elif first in held and mnemonic not in ('cmp', 'test', 'push') and not args.startswith(('qword ptr', 'dword ptr')):
            held.discard(first)
    if process.wait():
        raise ValueError(f'llvm-objdump failed on {kernel.path}')
    return {k: sorted(v) for k, v in uses.items()}


def kc_point(kernel, va):
    """A standalone-kernel position as the Boot KC step needs it: segment and offset."""
    segment, offset = kernel.segment_of(va)
    return dict(segment=segment, offset=hex(offset), kernel_address=hex(va))


class Port:
    def __init__(self, source, old, old_kc, new, build):
        self.source, self.old, self.old_kc, self.new, self.build = Path(source), old, old_kc, new, build
        self.profile = json.loads((self.source/'profile.json').read_text())
        self.evidence = {}
        old_kc.check(old)
        self.kernel_bound = {}

    def load(self, name):
        definition = json.loads((self.source/name).read_text())
        bound = definition.get('kernel_sha256')
        if bound is not None and bound != self.old.sha256:
            raise Missing(name, f'the source profile is bound to kernel {bound}, not --old-kernel {self.old.sha256}')
        return definition

    def ported(self, name, definition, evidence):
        definition = dict(definition)
        if 'kernel_sha256' in definition:
            definition['kernel_sha256'] = self.new.sha256
        if 'evidence' in definition:
            definition['evidence'] = {self.profile.get('userspace_build', 'source'): definition['evidence'],
                                      'port': f'kernel-fixes port-profile: see port.json "{name}"'}
        self.evidence[name] = evidence
        return definition

    # Kernel-only definitions -------------------------------------------------------------------------------

    def amfi(self):
        name = 'amfi-layout.json'
        d = self.load(name)
        old, new = pointer_uses(self.old, '_amfi'), pointer_uses(self.new, '_amfi')
        if not old:
            raise Missing(name, 'no AMFI interface use found in the old kernel')
        changed = sorted(f for f in set(old) | set(new) if old.get(f) != new.get(f))
        if changed:
            raise Missing(name, 'AMFI interface slot uses differ in ' + ', '.join(
                f'{f} {old.get(f)} -> {new.get(f)}' for f in changed[:6]))
        slots = sorted({f'{kind} {slot:#x}' for uses in new.values() for kind, slot in uses})
        return self.ported(name, d, dict(method='every function reading the _amfi interface uses the same slots',
                                         functions=len(new), slots=slots))

    def exec_entry(self):
        name = 'commpage-exec-entry.json'
        d = self.load(name)
        prefix = bytes.fromhex(d['prefix_hex'])
        for kernel in (self.old, self.new):
            if kernel.read(kernel.address(d['symbol']), len(prefix)) != prefix:
                raise Missing(name, f'{d["symbol"]} prologue differs in kernel {kernel.uuid}')
        same = require_same(self.old, self.new, d['symbol'], name)
        return self.ported(name, d, dict(method='same function code; replayed prologue identical', **same))

    def flags_tolerance(self):
        values = [int(self.profile['fixed_beyond_max'], 0), int(self.profile['fixed_beyond_max'], 0)
                  | int(self.profile['map_jit_flag'], 0)]

        def tolerate(x, y):
            mx = re.match(r'test (e[a-z]+|r\d+d), (0x[0-9a-f]+)$', x.text.split('##')[0].strip())
            my = re.match(r'test (e[a-z]+|r\d+d), (0x[0-9a-f]+)$', y.text.split('##')[0].strip())
            if not (mx and my and mx.group(1) == my.group(1)):
                return False
            a, b = int(mx.group(2), 16), int(my.group(2), 16)
            return all((v & 0xffffffff & a) == (v & 0xffffffff & b) for v in values)
        return tolerate

    def vm_flags(self):
        """fixed_beyond_max and map_jit_flag: the kernel's VM flag handling is unchanged for these values."""
        tolerate, evidence = self.flags_tolerance(), {}
        jit = int(self.profile['map_jit_flag'], 0)
        for function in ('_vm_map_enter_impl', '_vm_map_enter_mem_object'):
            evidence[function] = require_same(self.old, self.new, function, 'vm flags', tolerate)
        code = decode(self.new, *self.new.function('_vm_map_enter_impl'))
        if not any(re.match(rf'movabs r[a-z0-9]+, {jit:#x}$', i.text.split('##')[0].strip()) for i in code):
            raise Missing('map_jit_flag', f'{jit:#x} is not a constant of the new _vm_map_enter_impl')
        self.evidence['profile.json'] = dict(method='vm_map_enter_impl and vm_map_enter_mem_object same code '
                                             '(flag masks may differ only where these values give the same result); '
                                             'map_jit_flag tested in vm_map_enter_impl', **evidence)
        return self.profile['fixed_beyond_max'], self.profile['map_jit_flag']

    def fixed_entry(self):
        name = 'commpage-fixed-entry.json'
        d = self.load(name)
        address, prefix = int(d['address'], 0), bytes.fromhex(d['prefix_hex'])
        if self.old.read(address, len(prefix)) != prefix:
            raise Missing(name, 'the old kernel has other bytes at the captured locator')
        # The locator from its entry through its end > max rejection (evidence: entry+0xac) must be unchanged.
        found = locate(self.old, self.new, name, address, cover=0xad, minimum=len(prefix))
        if self.new.read(found.new, len(prefix)) != prefix:
            raise Missing(name, 'the new locator has another prologue')
        return self.ported(name, dict(d, address=hex(found.new)),
                           dict(method='masked signature of the locator through its bound rejection',
                                old=hex(address), new=hex(found.new), window=[hex(v) for v in found.window]))

    def rename(self):
        name = 'sandbox-rename-layout.json'
        d = self.load(name)
        register, ret = bytes.fromhex(d['register_prefix_hex']), bytes.fromhex(d['return_prefix_hex'])
        evidence = dict(method='same vn_rename and mac_policy_register code; sole vn_rename epilogue')
        for function in ('_mac_policy_register', '_vn_rename'):
            evidence[function] = require_same(self.old, self.new, function, name)
        for kernel in (self.old, self.new):
            if kernel.read(kernel.address('_mac_policy_register'), len(register)) != register:
                raise Missing(name, f'mac_policy_register prologue differs in kernel {kernel.uuid}')
        addresses = []
        for kernel in (self.old, self.new):
            start, end = kernel.function('_vn_rename')
            body = kernel.read(start, end-start)
            hits = [start+m.start() for m in re.finditer(re.escape(ret), body)]
            if len(hits) != 1:
                raise Missing(name, f'vn_rename has {len(hits)} copies of the return epilogue in kernel {kernel.uuid}')
            addresses.append(hits[0])
        if addresses[0] != int(d['return_address'], 0):
            raise Missing(name, 'the recipe does not reproduce the source return_address')
        evidence.update(old_return=hex(addresses[0]), new_return=hex(addresses[1]))
        return self.ported(name, dict(d, return_address=hex(addresses[1])), evidence)

    def endpoint(self):
        name = 'endpoint-port-policy.json'
        d = self.load(name)
        function = '_mac_proc_check_service_port_derive'
        same = require_same(self.old, self.new, function, name)
        code = decode(self.new, *self.new.function(function))
        slot = f'+ {d["callback_offset"]:#x}]'
        if not any(slot in i.text for i in code):
            raise Missing(name, f'{function} does not read policy_ops{slot[:-1]}')
        return self.ported(name, d, dict(method='same function code; reads the policy ops slot', **same))

    # Boot KC definitions ---------------------------------------------------------------------------------

    def standalone(self, kc_address):
        return self.old_kc.to_kernel(self.old, int(kc_address, 0))

    def named(self, item, kc_address):
        """A KC address the old kernel names: the same symbol in the new kernel."""
        name = self.old.name_at(self.standalone(kc_address))
        if name is None:
            return None
        return dict(kc_point(self.new, self.new.address(name)), symbol=name)

    def point(self, item, kc_address, cover=0, refs=None):
        found = locate(self.old, self.new, item, self.standalone(kc_address), cover)
        if refs is not None:
            for a, b in found.refs.items():
                if refs.setdefault(a, b) != b:
                    raise Missing(item, f'{a:#x} maps to two new addresses')
        return dict(kc_point(self.new, found.new), signature=[hex(v) for v in found.window], length=cover)

    def data(self, item, kc_address, refs):
        named = self.named(item, kc_address)
        if named:
            return named
        old = self.standalone(kc_address)
        if old not in refs:
            raise Missing(item, f'{old:#x} has no symbol and no reference from the matched code')
        return dict(kc_point(self.new, refs[old]), referenced_by_matched_code=hex(old))

    def vm_test(self):
        name = 'vm-test-layout.json'
        d = json.loads((self.source/name).read_text())
        recipe = {}
        for key, value in d.items():
            if key.startswith('_'):
                if self.old_kc.from_kernel(self.old, self.old.address(key)) != int(value, 0):
                    raise Missing(f'{name} {key}', 'the old KC address is not this symbol')
                recipe[key] = dict(kc_point(self.new, self.new.address(key)), symbol=key)
            else:
                recipe[key] = self.point(f'{name} {key}', value)
        return recipe

    def early_vm(self):
        name = 'early-vm.json'
        d = json.loads((self.source/name).read_text())
        if d['collection_sha256'] != self.old_kc.sha256 or d['kernel_uuid'] != self.old.uuid:
            raise Missing(name, 'the source capture is not for --old-kernel/--old-boot-kc')
        refs = {}
        recipe = dict(site=self.point(f'{name} site', d['site_address'], len(d['original'])//2, refs))
        if 'range_call' in d:
            recipe['range_call'] = self.point(f'{name} range_call', d['range_call']['address'],
                                              len(d['range_call']['original'])//2, refs)
        if 'pv_aliases' in d:
            hook = d['pv_aliases']['hook']
            recipe['pv_hook'] = self.point(f'{name} pv_aliases.hook', hook['address'], len(hook['original'])//2, refs)
            cave = d['pv_aliases']['cave']
            if self.standalone(cave['address'])+cave['capacity'] != self.old.text[0]:
                raise Missing(f'{name} pv_aliases.cave', 'the source cave does not end at __text')
            start = self.new.text[0]-cave['capacity']
            header_end = self.new.segments['__TEXT'][0]+32+struct.unpack_from('<I', self.new.data, 20)[0]
            if start < header_end or any(self.new.read(start, cave['capacity'])):
                raise Missing(f'{name} pv_aliases.cave', 'the new kernel has no zero padding of that size before __text')
            recipe['pv_cave'] = dict(kc_point(self.new, start), capacity=cave['capacity'],
                                     tail=(len(cave['original'])//2)-cave['capacity'])
            recipe['pv_addresses'] = {k: self.data(f'{name} pv {k}', v, refs)
                                      for k, v in d['pv_aliases']['addresses'].items()}
        recipe['addresses'] = {k: self.data(f'{name} {k}', v, refs) for k, v in d['addresses'].items()}
        return recipe


def resolve(point, kc):
    segment = kc.kernel_segments[point['segment']]
    return segment[0]+int(point['offset'], 0)


def finish_early_vm(source, recipe, new, kc):
    """early-vm.json for the new Boot KC: addresses through its kernel placement, bytes read from it."""
    kc.check(new)
    d = json.loads(json.dumps(source))

    def code(item, point, length):
        va = resolve(point, kc)
        sa = int(point['kernel_address'], 0)
        ins = decode(new, sa, sa+length+16)
        data, mask = masked(ins, sa, sa+length)
        actual = kc.read(va, length)
        if not masked_equal(actual, data, mask):
            raise Missing(item, 'the Boot KC bytes differ from the kernel code (masked)')
        return va, kc.fileoff(va), actual

    va, off, raw = code('site', recipe['site'], len(source['original'])//2)
    d.update(kernel_uuid=new.uuid, collection_sha256=kc.sha256, darwin_version=new.darwin,
             site_address=hex(va), site_offset=hex(off), original=raw.hex(),
             addresses={k: hex(resolve(v, kc)) for k, v in recipe['addresses'].items()})
    if 'range_call' in recipe:
        va, off, raw = code('range_call', recipe['range_call'], len(source['range_call']['original'])//2)
        d['range_call'].update(address=hex(va), offset=hex(off), original=raw.hex())
    if 'pv_cave' in recipe:
        va, off, raw = code('pv hook', recipe['pv_hook'], len(source['pv_aliases']['hook']['original'])//2)
        d['pv_aliases']['hook'].update(address=hex(va), offset=hex(off), original=raw.hex())
        cave = recipe['pv_cave']
        va = resolve(cave, kc)
        d['pv_aliases']['cave'].update(address=hex(va), offset=hex(kc.fileoff(va)),
                                       original=kc.read(va, cave['capacity']+cave['tail']).hex())
        d['pv_aliases']['addresses'] = {k: hex(resolve(v, kc)) for k, v in recipe['pv_addresses'].items()}
    d['evidence'] = {'source': source.get('evidence'),
                     'port': 'kernel-fixes port-profile: positions from kc-recipe.json, bytes from the Boot KC'}
    return d


def write(path, value):
    path.write_text(json.dumps(value, indent=2)+'\n')


def port(source, old_kernel, old_kc, new_kernel, build, out, new_kc=None, generators=()):
    """Write the ported profile to out (a new folder). `generators` are (definition, command) pairs run after the
    kernel definitions with `--out out/definition` appended. Returns the PENDING definitions."""
    old, new = Kernel(old_kernel), Kernel(new_kernel)
    p = Port(source, old, Collection(old_kc), new, build)
    out = Path(out)
    definitions = dict(p.profile['definitions'])
    results = {'amfi-layout.json': p.amfi(), 'commpage-exec-entry.json': p.exec_entry(),
               'commpage-fixed-entry.json': p.fixed_entry(), 'sandbox-rename-layout.json': p.rename(),
               'endpoint-port-policy.json': p.endpoint()}
    fixed, jit = p.vm_flags()
    recipe = {'early-vm.json': p.early_vm(), 'vm-test-layout.json': p.vm_test()}
    unknown = set(definitions)-set(results)-set(recipe)-{'sandbox-op-layout.json', 'iosurface-layout.json'}
    if unknown:
        raise Missing(', '.join(sorted(unknown)), 'no port recipe for this definition')
    pending = {}
    if new_kc:
        kc = Collection(new_kc)
        kc.check(new)
        source_vm = json.loads((Path(source)/'early-vm.json').read_text())
        results['early-vm.json'] = finish_early_vm(source_vm, recipe['early-vm.json'], new, kc)
        results['vm-test-layout.json'] = {k: hex(resolve(v, kc)) for k, v in recipe['vm-test-layout.json'].items()}
    else:
        for name in recipe:
            pending[name] = 'needs the new Boot KC: port-profile --new-boot-kc KC'
    out.mkdir(parents=True, exist_ok=False)
    for name, value in results.items():
        write(out/name, value)
    write(out/'kc-recipe.json', dict(kernel_uuid=new.uuid, kernel_sha256=new.sha256, definitions=recipe,
                                    early_vm_template=json.loads((Path(source)/'early-vm.json').read_text())))
    write(out/'port.json', dict(source=str(Path(source).name), old_kernel=dict(uuid=old.uuid, sha256=old.sha256,
                                darwin=old.darwin, xnu=old.xnu),
                                new_kernel=dict(uuid=new.uuid, sha256=new.sha256, darwin=new.darwin, xnu=new.xnu),
                                old_boot_kc=p.old_kc.sha256, new_boot_kc=Collection(new_kc).sha256 if new_kc else None,
                                evidence=p.evidence))
    for name, command in generators:
        subprocess.run([*map(str, command), '--out', str(out/name)], check=True)
    for name in ('sandbox-op-layout.json', 'iosurface-layout.json'):
        if (out/name).exists():
            continue
        pending[name] = ('needs make sandbox-layout for the new userspace cache' if name.startswith('sandbox')
                         else 'needs the new Boot KC: kernel-fixes iosurface-layout --boot-kc KC')
    profile = dict(darwin='.'.join(new.darwin.split('.')[:2]), userspace_build=build,
                   kernel=dict(uuid=new.uuid, sha256=new.sha256, darwin_version=new.darwin, xnu=new.xnu),
                   officially_supported_models=p.profile['officially_supported_models'],
                   fixed_beyond_max=fixed, map_jit_flag=jit, ported_from=Path(source).name,
                   definitions={}, pending={})
    finalize(out, profile, pending)
    return pending


def finalize(out, profile=None, pending=None):
    """Record every present definition's sha256 in profile.json; definitions still absent stay pending."""
    out = Path(out)
    profile = profile or json.loads((out/'profile.json').read_text())
    pending = dict(profile.get('pending', {}) if pending is None else pending)
    for name in list(pending):
        if (out/name).exists():
            del pending[name]
    for name in sorted(p.name for p in out.glob('*.json')):
        if name not in ('profile.json', 'kc-recipe.json', 'port.json'):
            operation = json.loads((out/name).read_text()).get('operation')
            profile['definitions'][name] = dict(sha256=sha256((out/name).read_bytes()),
                                                source=operation or 'port-profile')
    profile['pending'] = pending
    write(out/'profile.json', profile)
    return pending


def complete(source, kernel_path, collection_path, out, generators=()):
    """Finish a captured kernel recipe using only this build's kernel and collection."""
    source, out = Path(source), Path(out)
    kernel, collection = Kernel(kernel_path), Collection(collection_path)
    recipe = json.loads((source/'kc-recipe.json').read_text())
    if recipe['kernel_sha256'] != kernel.sha256 or recipe['kernel_uuid'] != kernel.uuid:
        raise Missing('kernel recipe', 'does not describe the supplied kernel')
    collection.check(kernel)
    shutil.copytree(source, out)
    write(out/'early-vm.json', finish_early_vm(recipe['early_vm_template'],
          recipe['definitions']['early-vm.json'], kernel, collection))
    write(out/'vm-test-layout.json', {k: hex(resolve(v, collection))
          for k, v in recipe['definitions']['vm-test-layout.json'].items()})
    for name, command in generators:
        subprocess.run([*map(str, command), '--out', str(out/name)], check=True)
    return finalize(out)
