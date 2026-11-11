"""Code identity carrier: hao_codeid_decide in the x86_64 shared cache's Security.framework.

hao-runtime's libhao_codeid.a decides, after Security's own validity check, whether a requirement failure of a listed
code (an image-built hao stub or a native program the image re-signed ad hoc) is answered by the Apple-signed reference
the lists in /usr/lib/hao name (hao-runtime include/hao/codeid.h, include/hao/idlist.h). This module links that archive
and the hooks of `codeid/carrier.c` into unused, zero-filled space of the cache file that holds Security, and points the
two validity bodies at the hooks:

- `SecCodeCheckValidityWithErrors` and `SecStaticCodeCheckValidityWithErrors` begin with a jump to
  `hao_carrier_<name>`, which runs the original (its relocated prologue, then a jump back into Apple's body) and returns
  `hao_codeid_decide(code, requirement, status, errors)`.
- `SecCodeCheckValidity` and `SecStaticCodeCheckValidity` are required to be the cache's two-instruction forwarders
  into those bodies (`xor ecx, ecx` and a jump), so they and every caller inside Security (SecTaskValidateForRequirement,
  libxpc's dlsym'd checks) reach the hooks without an entry of their own.

The objects' code and constants go into the free tail of the subcache's __TEXT mapping, their writable data into the
free tail of its __DATA mapping. Calls and data references resolve to Security's own symbols, to Security's GOT slots
(so they follow dyld's root patching exactly as Security's own calls do) and otherwise to the defining library's export
in the same subcache. Nothing that would need a rebase (an absolute pointer) is accepted. Every site is checked by
shape; a cache that does not match stops the build (`MISSING/unsupported`). The caller re-signs the edited file.
"""
import re
import struct
from pathlib import Path

SECURITY = b'/System/Library/Frameworks/Security.framework/Versions/A/Security'
# body -> (hook, name the hook calls for the original)
HOOKS = {
    '_SecCodeCheckValidityWithErrors': ('_hao_carrier_SecCodeCheckValidityWithErrors',
                                        '_hao_carrier_original_SecCodeCheckValidityWithErrors'),
    '_SecStaticCodeCheckValidityWithErrors': ('_hao_carrier_SecStaticCodeCheckValidityWithErrors',
                                              '_hao_carrier_original_SecStaticCodeCheckValidityWithErrors'),
}
# forwarder -> body it must fall into: push rbp; mov rbp, rsp; xor ecx, ecx; pop rbp; jmp body
FORWARDERS = {'_SecCodeCheckValidity': '_SecCodeCheckValidityWithErrors',
              '_SecStaticCodeCheckValidity': '_SecStaticCodeCheckValidityWithErrors'}
FORWARDER_SHAPE = bytes.fromhex('554889e531c95de9')
# position-independent prologue instructions a hooked body may start with
PROLOGUE = [bytes.fromhex('55'), bytes.fromhex('4889e5'), *(bytes([0x41, r]) for r in range(0x50, 0x58)),
            *(bytes([r]) for r in (0x53, 0x56, 0x57))]
MARK = b'hao-codeid-carrier\0'          # leads the text blob, so a cache that already has it is recognisable


class Unsupported(ValueError):
    def __init__(self, text):
        super().__init__('MISSING/unsupported: code identity carrier: ' + text)


# --- the cache --------------------------------------------------------------------------------------------------

class Cache:
    """The x86_64 cache family in a directory: mappings of every file, reads by address, images of the main file."""

    def __init__(self, directory):
        self.files = []
        for path in sorted(Path(directory).glob('dyld_shared_cache_x86_64*')):
            if path.is_symlink() or path.suffix in ('.map', '.atlas', '.symbols'):
                continue
            with path.open('rb') as stream:
                head = stream.read(0x400)
                if not head.startswith(b'dyld_v1  x86_64'):
                    raise Unsupported(f'unexpected cache file {path}')
                offset, count = struct.unpack_from('<II', head, 16)
                stream.seek(offset)
                mappings = [struct.unpack('<QQQII', stream.read(32)) for _ in range(count)]
                slide_offset, slide_count = struct.unpack_from('<II', head, 0x138)
                stream.seek(slide_offset)
                slides = [struct.unpack('<QQQQQQII', stream.read(56)) for _ in range(slide_count)]
            self.files.append(dict(path=path, head=head, mappings=mappings, slides=slides))
        main = Path(directory)/'dyld_shared_cache_x86_64'
        if not self.files or self.files[0]['path'] != main:
            raise Unsupported('no dyld_shared_cache_x86_64')
        self.handles = {}
        with main.open('rb') as stream:
            stream.seek(448)                         # dyld_cache_header.imagesOffset/imagesCount
            offset, count = struct.unpack('<II', stream.read(8))
            stream.seek(offset)
            table = stream.read(count * 32)
            self.images = []
            for row in range(count):
                address, _, _, name_offset, _ = struct.unpack_from('<QQQII', table, row * 32)
                stream.seek(name_offset)
                self.images.append((address, stream.read(1024).split(b'\0', 1)[0]))

    def locate(self, address, size=1):
        found = [(f, f['path'], m[2] + address - m[0]) for f in self.files for m in f['mappings']
                 if m[0] <= address and address + size <= m[0] + m[1]]
        if len(found) != 1:
            raise Unsupported(f'unmapped address {address:#x}')
        return found[0]

    def read(self, address, size):
        _, path, offset = self.locate(address, size)
        stream = self.handles.get(path) or self.handles.setdefault(path, path.open('rb'))
        stream.seek(offset)
        data = stream.read(size)
        if len(data) != size:
            raise Unsupported('truncated cache')
        return data

    def close(self):
        for stream in self.handles.values():
            stream.close()
        self.handles = {}

    def image(self, name):
        found = [address for address, path in self.images if path == name]
        if len(found) != 1:
            raise Unsupported('expected one cached image ' + name.decode())
        return Image(self, found[0], name)

    def mapping_of(self, address):
        """(file record, mapping) holding address."""
        for f in self.files:
            for m in f['mappings']:
                if m[0] <= address < m[0] + m[1]:
                    return f, m
        raise Unsupported(f'unmapped address {address:#x}')


class Image:
    def __init__(self, cache, base, name):
        self.cache, self.base, self.name = cache, base, name
        header = cache.read(base, 32)
        magic, cpu, _, filetype, count, size = struct.unpack_from('<IIIIII', header)
        if magic != 0xfeedfacf or cpu != 0x01000007:
            raise Unsupported(f'{name.decode()}: not an x86_64 Mach-O')
        commands = cache.read(base + 32, size)
        self.segments, self.sections, self.symtab, self.dysymtab, self.trie = {}, {}, None, None, None
        at = 0
        for _ in range(count):
            command, length = struct.unpack_from('<II', commands, at)
            if command == 0x19:
                segment = commands[at + 8:at + 24].rstrip(b'\0').decode()
                self.segments[segment] = struct.unpack_from('<QQQQ', commands, at + 24)
                for i in range(struct.unpack_from('<I', commands, at + 64)[0]):
                    row = at + 72 + i * 80
                    section = commands[row:row + 16].rstrip(b'\0').decode()
                    address, size_, _, _, _, _, flags, reserved1 = struct.unpack_from('<QQIIIIII', commands, row + 32)
                    self.sections[(segment, section)] = (address, size_, flags, reserved1)
            elif command == 0x2:
                self.symtab = struct.unpack_from('<IIII', commands, at + 8)
            elif command == 0xb:
                self.dysymtab = struct.unpack_from('<18I', commands, at + 8)
            elif command == 0x80000033:
                self.trie = struct.unpack_from('<II', commands, at + 8)
            at += length

    def linkedit(self, offset, size):
        address, _, file_offset, _ = self.segments['__LINKEDIT']
        return self.cache.read(address + offset - file_offset, size)

    def _strings(self):
        symoff, nsyms, stroff, strsize = self.symtab
        return self.linkedit(symoff, nsyms * 16), self.linkedit(stroff, strsize)

    def defined(self):
        """External symbols this image defines in a section: name -> address."""
        table, strings = self._strings()
        out = {}
        for i in range(len(table) // 16):
            strx, kind, sect, _, value = struct.unpack_from('<IBBHQ', table, i * 16)
            if kind & 0xe0 == 0 and kind & 0x0e == 0x0e and kind & 1 and sect:
                out[strings[strx:strings.index(b'\0', strx)].decode()] = value
        return out

    def stubs(self):
        """Its live symbol stubs named by the indirect symbol table: name -> stub address. A cached image's own __got
        is left empty by the cache builder (its stubs jump through the subcache's shared GOT instead), so only stubs
        are used: each must be `jmp *slot(%rip)` with a bound slot."""
        table, strings = self._strings()
        indirect = self.linkedit(self.dysymtab[12], self.dysymtab[13] * 4)
        out = {}
        for (segment, section), (address, size, flags, reserved1) in self.sections.items():
            if flags & 0xff != 8:                    # S_SYMBOL_STUBS
                continue
            code = self.cache.read(address, size)
            for k in range(size // 6):
                index = struct.unpack_from('<I', indirect, (reserved1 + k) * 4)[0]
                if index & 0xc0000000 or index * 16 >= len(table) or code[6 * k:6 * k + 2] != b'\xff\x25':
                    continue
                slot = address + 6 * k + 6 + struct.unpack_from('<i', code, 6 * k + 2)[0]
                if not any(self.cache.read(slot, 8)):
                    continue
                strx = struct.unpack_from('<I', table, index * 16)[0]
                out.setdefault(strings[strx:strings.index(b'\0', strx)].decode(), address + 6 * k)
        return out

    def export(self, name):
        """Address of a regular export of this image, None if it has none (re-exports, resolvers: None)."""
        if not self.trie:
            return None
        data = self.linkedit(*self.trie)
        target, node = name.encode(), 0
        while True:
            size, at = _uleb(data, node)
            if not target:
                if not size:
                    return None
                flags, at = _uleb(data, at)
                if flags & ~0x4 or flags & 0x3:       # only regular, non-weak-irrelevant exports (0 or 4)
                    return None
                offset, _ = _uleb(data, at)
                return self.base + offset
            at += size
            children, at = data[at], at + 1
            for _ in range(children):
                end = data.index(b'\0', at)
                edge = data[at:end]
                child, at = _uleb(data, end + 1)
                if target.startswith(edge):
                    target, node = target[len(edge):], child
                    break
            else:
                return None


def _uleb(data, at):
    value = shift = 0
    while True:
        byte = data[at]
        at += 1
        value |= (byte & 0x7f) << shift
        shift += 7
        if byte < 0x80:
            return value, at


# --- objects ----------------------------------------------------------------------------------------------------

def read_archive(data):
    """Members (name, bytes) of a BSD ar archive, without its symbol table."""
    if not data.startswith(b'!<arch>\n'):
        raise Unsupported('not an ar archive')
    at, out = 8, []
    while at + 60 <= len(data):
        name, size = data[at:at + 16].decode().strip(), int(data[at + 48:at + 58].decode().strip())
        body = data[at + 60:at + 60 + size]
        if name.startswith('#1/'):
            length = int(name[3:])
            name, body = body[:length].split(b'\0', 1)[0].decode(), body[length:]
        if not name.startswith('__.SYMDEF'):
            out.append((name, body))
        at += 60 + size + (size & 1)
    return out


SKIP_SECTIONS = {('__LD', '__compact_unwind'), ('__TEXT', '__eh_frame')}


class Object:
    """An x86_64 MH_OBJECT: sections, symbols, relocations."""

    def __init__(self, name, data):
        self.name = name
        magic, cpu, _, filetype, count, size, _ = struct.unpack_from('<IIIIIII', data)
        if magic != 0xfeedfacf or cpu != 0x01000007 or filetype != 1:
            raise Unsupported(f'{name}: not an x86_64 object')
        self.sections, self.symbols, at = [], [], 32
        for _ in range(count):
            command, length = struct.unpack_from('<II', data, at)
            if command == 0x19:
                for i in range(struct.unpack_from('<I', data, at + 64)[0]):
                    row = at + 72 + i * 80
                    section = data[row:row + 16].rstrip(b'\0').decode()
                    segment = data[row + 16:row + 32].rstrip(b'\0').decode()
                    address, size_, offset, align, reloff, nreloc, flags = struct.unpack_from('<QQIIIII', data, row + 32)
                    zerofill = flags & 0xff in (1, 0xc)
                    relocs = [struct.unpack_from('<iI', data, reloff + 8 * k) for k in range(nreloc)]
                    self.sections.append(dict(segment=segment, section=section, address=address, size=size_,
                                              align=1 << align, flags=flags, relocs=relocs,
                                              data=bytes(size_) if zerofill else data[offset:offset + size_]))
            elif command == 0x2:
                symoff, nsyms, stroff, _ = struct.unpack_from('<IIII', data, at + 8)
                for k in range(nsyms):
                    strx, kind, sect, desc, value = struct.unpack_from('<IBBHQ', data, symoff + 16 * k)
                    if kind & 0xef == 0x01 and value:        # undefined, external, with a size: a common symbol
                        raise Unsupported(f'{name}: common symbol (build with -fno-common)')
                    self.symbols.append(dict(name=data[stroff + strx:data.index(b'\0', stroff + strx)].decode(),
                                             kind=kind, sect=sect, value=value))
            at += length

    def section_at(self, address):
        for index, s in enumerate(self.sections):
            if s['address'] <= address < s['address'] + max(s['size'], 1):
                return index
        raise Unsupported(f'{self.name}: no section holds {address:#x}')


# --- linking ----------------------------------------------------------------------------------------------------

def _region(cache, image, kind):
    """The free tail of the subcache mapping that holds image's __TEXT (kind 'text') or __DATA (kind 'data'):
    after the last image segment, zero-filled, no rebase in it, and named by no field of the file's header."""
    anchor = image.segments['__TEXT' if kind == 'text' else '__DATA'][0]
    record, mapping = cache.mapping_of(anchor)
    want = 5 if kind == 'text' else 3
    if mapping[4] != want:
        raise Unsupported(f'{kind} mapping protection {mapping[4]}')
    start, end = mapping[0], mapping[0] + mapping[1]
    last = start
    for address, _ in cache.images:
        other = Image(cache, address, b'')
        for name, (va, vs, _, _) in other.segments.items():
            if name != '__LINKEDIT' and va < end and va + vs > start:
                last = max(last, va + vs)
    if end - last < 0x400:
        raise Unsupported(f'no free {kind} tail ({end - last:#x} bytes)')
    if cache.read(last, end - last) != bytes(end - last):
        raise Unsupported(f'{kind} tail at {last:#x} is not zero-filled')
    file_lo = mapping[2] + last - start
    file_hi = mapping[2] + mapping[1]
    head = record['head'][:struct.unpack_from('<I', record['head'], 16)[0]]
    for k in range(16, len(head) - 7, 8):
        value = struct.unpack_from('<Q', head, k)[0]
        if last <= value < end or (value and file_lo <= value < file_hi):
            raise Unsupported(f'{kind} tail is named by the cache header (+{k:#x})')
    if kind == 'data':
        _no_rebase(cache, record, mapping, last, end)
    return _align(last + 0x10, 0x40), end


def _no_rebase(cache, record, mapping, lo, hi):
    for address, size, file_offset, slide_offset, slide_size, _, _, _ in record['slides']:
        if address != mapping[0]:
            continue
        if not slide_size:
            return
        with record['path'].open('rb') as stream:
            stream.seek(slide_offset)
            info = stream.read(slide_size)
        version, page_size, starts_offset, starts_count = struct.unpack_from('<IIII', info)
        if version != 2:
            raise Unsupported(f'slide info v{version}')
        delta_mask = struct.unpack_from('<Q', info, 24)[0]
        shift = (delta_mask & -delta_mask).bit_length() - 1 - 2
        for page in range((lo - address) // page_size, min(starts_count, (hi - address + page_size - 1) // page_size)):
            start = struct.unpack_from('<H', info, starts_offset + 2 * page)[0]
            if start == 0x4000:
                continue
            if start & 0x8000:
                raise Unsupported('slide info page extras in the data tail')
            offset = start * 4
            while True:
                where = address + page * page_size + offset
                if lo <= where < hi:
                    raise Unsupported(f'rebase at {where:#x} in the data tail')
                delta = (struct.unpack('<Q', cache.read(where, 8))[0] & delta_mask) >> shift
                if not delta:
                    break
                offset += delta
        return
    raise Unsupported('no slide info for the data mapping')


def _prologue(code):
    """Length of whole position-independent prologue instructions covering at least 5 bytes."""
    at = 0
    while at < 5:
        for shape in PROLOGUE:
            if code.startswith(shape, at):
                at += len(shape)
                break
        else:
            raise Unsupported('hooked body prologue ' + code[:8].hex())
    return at


def _align(value, alignment):
    return (value + alignment - 1) // alignment * alignment


def link(cache, objects, text=None, data=None):
    """Lay out and relocate objects for the cache. text/data: (start, end) to place them (default: the free tails).
    Returns dict(text=(address, bytes), data=(address, bytes), entries=[(address, before, after, reason)], ...)."""
    security = cache.image(SECURITY)
    defined = security.defined()
    stubs = security.stubs()
    text = text or _region(cache, security, 'text')
    data = data or _region(cache, security, 'data')
    libraries = [cache.image(path) for address, path in cache.images
                 if (path.startswith(b'/usr/lib/system/') or path in (
                     b'/System/Library/Frameworks/CoreFoundation.framework/Versions/A/CoreFoundation',
                     b'/usr/lib/libSystem.B.dylib'))
                 and cache.mapping_of(address)[0] is cache.mapping_of(security.base)[0]]

    # layout: text blob = mark, stubs, trampolines, object text/constants; data blob = writable sections
    placed, globals_ = {}, {}
    text_parts, data_parts = [MARK], []
    text_at, data_at = text[0] + len(MARK), data[0]
    for obj_index, obj in enumerate(objects):
        for index, s in enumerate(obj.sections):
            key = (s['segment'], s['section'])
            if key in SKIP_SECTIONS or s['segment'].startswith('__DWARF'):
                continue
            writable = s['segment'] == '__DATA'
            if not writable and s['segment'] != '__TEXT':
                raise Unsupported(f'{obj.name}: section {key}')
            if writable:
                address = _align(data_at, s['align'])
                data_parts.append(bytes(address - data_at)); data_at = address + s['size']
                data_parts.append(s['data'])
            else:
                address = _align(text_at, s['align'])
                text_parts.append(b'\xcc' * (address - text_at)); text_at = address + s['size']
                text_parts.append(s['data'])
            placed[(obj_index, index)] = address
        for symbol in obj.symbols:
            if symbol['kind'] & 0xe0 or symbol['kind'] & 0x0e != 0x0e:
                continue
            index = symbol['sect'] - 1
            if (obj_index, index) not in placed:
                continue
            if symbol['kind'] & 1:
                if symbol['name'] in globals_:
                    raise Unsupported('duplicate symbol ' + symbol['name'])
                globals_[symbol['name']] = (obj_index, index, symbol['value'])

    def address_of(obj_index, original):
        obj = objects[obj_index]
        index = obj.section_at(original)
        if (obj_index, index) not in placed:
            raise Unsupported(f'{obj.name}: reference into a dropped section at {original:#x}')
        return placed[(obj_index, index)] + original - obj.sections[index]['address']

    # hooks: trampolines (relocated prologue, jump back) and entry jumps
    trampolines, entries, records = {}, [], {}
    for body, (hook, original) in HOOKS.items():
        if body not in defined:
            raise Unsupported('Security does not define ' + body)
        start = defined[body]
        code = cache.read(start, 16)
        size = _prologue(code)
        _no_branch_into(cache, security, start, size)
        address = _align(text_at, 16)
        text_parts.append(b'\xcc' * (address - text_at))
        blob = code[:size] + b'\xe9' + struct.pack('<i', start + size - (address + size + 5))
        text_parts.append(blob); text_at = address + len(blob)
        trampolines[original] = address
        records[body] = dict(body=start, prologue=code[:size].hex(), trampoline=address)
    for forwarder, body in FORWARDERS.items():
        start = defined.get(forwarder)
        code = cache.read(start, 12) if start else b''
        if not code.startswith(FORWARDER_SHAPE) or start + 12 + struct.unpack_from('<i', code, 8)[0] != defined[body]:
            raise Unsupported(f'{forwarder} is not the forwarder into {body}')

    used_stubs = set()

    def external(name, function):
        """Address a reference to name resolves to: a trampoline, Security's own symbol, Security's stub for an
        imported function (it follows dyld's binding and root patching as Security's calls do), else the regular
        export of a system library in the same subcache."""
        if name in trampolines:
            return trampolines[name]
        if name in defined:
            return defined[name]
        if function and name in stubs:
            used_stubs.add(name)
            return stubs[name]
        found = {lib.export(name) for lib in libraries} - {None}
        if len(found) != 1:
            raise Unsupported(f'{name}: {len(found)} definitions in the subcache')
        return found.pop()

    # relocate
    patches = []                                 # (absolute address, 4 bytes) applied after joining
    opcode_patches = []
    for obj_index, obj in enumerate(objects):
        for index, s in enumerate(obj.sections):
            if (obj_index, index) not in placed:
                if s['relocs'] and (s['segment'], s['section']) not in SKIP_SECTIONS and \
                        not s['segment'].startswith('__DWARF'):
                    raise Unsupported(f'{obj.name}: relocations in dropped {s["section"]}')
                continue
            base, relocs, k = placed[(obj_index, index)], s['relocs'], 0
            while k < len(relocs):
                offset, info = relocs[k]
                symbolnum, pcrel, length, extern, kind = (info & 0xffffff, info >> 24 & 1, info >> 25 & 3,
                                                          info >> 27 & 1, info >> 28)
                where = base + offset
                field = struct.unpack_from('<i', s['data'], offset)[0] if length == 2 else None

                def local_original(symbolnum, extern):
                    """Object address of a symbol or section this object defines."""
                    if not extern:
                        return obj.sections[symbolnum - 1]['address']
                    symbol = obj.symbols[symbolnum]
                    if symbol['kind'] & 0x0e != 0x0e:
                        raise Unsupported(f'{obj.name}: difference with {symbol["name"]}')
                    return symbol['value']

                def target_of(symbolnum, extern):
                    if not extern:
                        return None, symbolnum - 1
                    symbol = obj.symbols[symbolnum]
                    if symbol['kind'] & 0x0e == 0x0e:
                        return ('local', address_of(obj_index, symbol['value'])), None
                    if symbol['name'] in globals_:
                        o, i, v = globals_[symbol['name']]
                        return ('local', placed[(o, i)] + v - objects[o].sections[i]['address']), None
                    return ('external', symbol['name']), None

                if kind in (1, 2, 6, 7, 8):              # SIGNED, BRANCH, SIGNED_1/2/4
                    if not pcrel or length != 2:
                        raise Unsupported(f'{obj.name}: relocation shape {kind}')
                    bias = {1: 0, 2: 0, 6: 1, 7: 2, 8: 4}[kind]
                    if extern:
                        (how, value), _ = target_of(symbolnum, 1)
                        addend = field + bias
                        if how == 'external':
                            value = external(value, kind == 2)
                        target = value + addend
                    else:
                        target = address_of(obj_index, s['address'] + offset + 4 + bias + field)
                    patches.append((where, target - (where + 4 + bias)))
                elif kind in (3, 4):                     # GOT_LOAD, GOT: the address itself, by leaq
                    (how, value), _ = target_of(symbolnum, 1)
                    value = value if how == 'local' else external(value, False)
                    if kind != 3 or s['data'][offset - 2] != 0x8b:
                        raise Unsupported(f'{obj.name}: GOT reference other than movq x@GOTPCREL(%rip)')
                    opcode_patches.append(where - 2)              # movq x@GOTPCREL(%rip) -> leaq x(%rip)
                    patches.append((where, value + field - (where + 4)))
                elif kind == 5:                          # SUBTRACTOR + UNSIGNED, 32 bit: T - S
                    if length != 2 or k + 1 >= len(relocs):
                        raise Unsupported(f'{obj.name}: 64-bit difference')
                    offset2, info2 = relocs[k + 1]
                    if offset2 != offset or info2 >> 28 != 0:
                        raise Unsupported(f'{obj.name}: unpaired SUBTRACTOR')
                    sub_original = local_original(symbolnum, extern)
                    sub_address = address_of(obj_index, sub_original)
                    extern2, symbolnum2 = info2 >> 27 & 1, info2 & 0xffffff
                    if extern2:                          # the field is the addend: T + A - S
                        result = address_of(obj_index, local_original(symbolnum2, 1)) + field - sub_address
                    else:                                # the field is the difference itself
                        result = address_of(obj_index, sub_original + field) - sub_address
                    patches.append((where, result))
                    k += 1
                else:
                    raise Unsupported(f'{obj.name}: relocation type {kind} (an absolute pointer needs a rebase)')
                k += 1

    blob_text = bytearray(b''.join(text_parts))
    blob_data = bytearray(b''.join(data_parts))
    for where, value in patches:
        if not -(1 << 31) <= value < (1 << 31):
            raise Unsupported(f'reference at {where:#x} out of range')
        blob, base = (blob_text, text[0]) if text[0] <= where < text[1] else (blob_data, data[0])
        struct.pack_into('<i', blob, where - base, value)
    for where in opcode_patches:
        blob, base = (blob_text, text[0]) if text[0] <= where < text[1] else (blob_data, data[0])
        blob[where - base] = 0x8d
    if text[0] + len(blob_text) > text[1] or data[0] + len(blob_data) > data[1]:
        raise Unsupported(f'carrier does not fit ({len(blob_text)} text, {len(blob_data)} data bytes)')
    symbols = {name: placed[(o, i)] + v - objects[o].sections[i]['address'] for name, (o, i, v) in globals_.items()}
    for body, (hook, original) in HOOKS.items():
        if hook not in symbols:
            raise Unsupported('objects do not define ' + hook)
        start = records[body]['body']
        size = len(bytes.fromhex(records[body]['prologue']))
        before = cache.read(start, size)
        after = b'\xe9' + struct.pack('<i', symbols[hook] - (start + 5)) + b'\xcc' * (size - 5)
        entries.append((start, before, after, f'{body[1:]} -> {hook[1:]}'))
        records[body]['hook'] = symbols[hook]
    return dict(text=(text[0], bytes(blob_text)), data=(data[0], bytes(blob_data)), entries=entries,
                symbols=symbols, hooks=records, stubs=sorted(used_stubs))


def _no_branch_into(cache, image, start, size):
    """No rel32 call/jump in the image's __text lands inside the bytes the entry jump replaces."""
    address, length, _, _ = image.sections[('__TEXT', '__text')]
    code = cache.read(address, length)
    for match in re.finditer(rb'[\xe8\xe9]|\x0f[\x80-\x8f]', code):
        at = match.end()
        if at + 4 > len(code):
            continue
        target = address + at + 4 + struct.unpack_from('<i', code, at)[0]
        if start < target < start + size:
            raise Unsupported(f'a branch at {address + match.start():#x} lands inside the hooked prologue')


def load_objects(folder):
    """carrier.o and the members of libhao_codeid.a from an artifact folder (row codeid_carrier), checked against the
    hashes its build.json records (the row names a folder, which carries no hash of its own)."""
    import hashlib
    import json
    folder = Path(folder)
    record = json.loads((folder/'build.json').read_text())
    for name, want in (('carrier.o', record['carrier_o']), ('libhao_codeid.a', record['hao_runtime']['libhao_codeid_a'])):
        if hashlib.sha256((folder/name).read_bytes()).hexdigest() != want:
            raise Unsupported(f'{folder/name} is not the {name} its build.json records')
    objects = [Object('carrier.o', (folder/'carrier.o').read_bytes())]
    objects += [Object(name, body) for name, body in read_archive((folder/'libhao_codeid.a').read_bytes())]
    return objects


def plan_cache_family(directory, folder=None, objects=None):
    """File-offset edits {path: [(offset, before, after, reason)]} and a summary, like the other cache repairs."""
    cache = Cache(directory)
    try:
        security = cache.image(SECURITY)
        text = _region(cache, security, 'text')
        result = link(cache, objects if objects is not None else load_objects(folder), text=text)
        plan = {}
        for address, before, after, reason in [
                (result['text'][0], bytes(len(result['text'][1])), result['text'][1], 'code identity carrier text'),
                (result['data'][0], bytes(len(result['data'][1])), result['data'][1], 'code identity carrier data'),
                *result['entries']]:
            if not after:
                continue
            _, path, offset = cache.locate(address, len(after))
            if cache.read(address, len(before)) != before:
                raise Unsupported(f'{reason}: bytes changed')
            plan.setdefault(path, []).append((offset, before, after, reason))
        summary = dict(text=f'{result["text"][0]:#x}+{len(result["text"][1]):#x}',
                       data=f'{result["data"][0]:#x}+{len(result["data"][1]):#x}',
                       hooks={body[1:]: f'{r["body"]:#x} -> {r["hook"]:#x}' for body, r in result['hooks'].items()},
                       stubs=len(result["stubs"]))
        return plan, summary
    finally:
        cache.close()
