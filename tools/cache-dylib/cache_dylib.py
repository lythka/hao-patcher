"""cache_dylib.py: turn one image of an x86_64 dyld shared cache back into a standalone, loadable dylib.

The method is the one OCLP's cache-cut libraries use (moraea's dsce): keep every segment at its cache address (so
code's RIP-relative references stay valid), page-align the segments, rebuild rebase and bind information from the
cache's slide info and the dependencies' export tries, give the image a private copy of the cache's uniqued GOT
slots it references, and undo the cache builder's Objective-C optimizations (selector and protocol uniquing, direct
selector offsets in method lists, preoptimized method caches, the optimized-by-dyld image flag). This is an
independent implementation; it refuses what it cannot rebuild instead of leaving cache addresses behind.

    python3.14 -I cache_dylib.py extract CACHE_DIR ARCH IMAGE_PATH OUT [--install-name PATH] [--report JSON]

The result is not signed; sign it (codesign -s -) after any further edit. check.py verifies the result.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dsc  # noqa: E402

PAGE = 0x1000
MH_DYLIB_IN_CACHE = 0x80000000
SG_READ_ONLY = 0x10
S_ZEROFILL = 0x1
S_SYMBOL_STUBS = 0x8
INDIRECT_SYMBOL_ABS = 0x40000000
N_SECT, N_EXT = 0xE, 0x1
VM_PROT_READ, VM_PROT_WRITE, VM_PROT_EXECUTE = 1, 2, 4

# method_list_t flags (objc4): relative entries, direct selector offsets (cache only), uniqued and sorted (fixed up)
METHOD_LIST_RELATIVE = 0x80000000
METHOD_LIST_DIRECT_SELECTORS = 0x40000000
METHOD_LIST_FIXED_UP = 0x3
# objc_image_info flags cleared: dyld categories optimized (0), optimized by dyld (3), optimized by the dyld closure (7).
# With bit 0 left set, libobjc skips the image's categories when it is loaded at launch (they count as attached by the
# cache builder), so e.g. Tahoe CoreWiFi's -[NSString redactedForWiFi] was missing in airportd; a dlopen attached them.
IMAGE_INFO_CLEAR = (1 << 0) | (1 << 3) | (1 << 7)
# protocol_t flags the cache builder sets on canonical copies: fixed up (30, 31), canonical (29)
PROTOCOL_CLEAR = 0xE0000000
STRING_SECTIONS = ('__objc_methname', '__objc_classname', '__objc_methtype', '__cstring')
EMPTY_CACHE = '__objc_empty_cache'
GOT_SEGMENT = '__CACHE_GOT'


def page_down(value):
    return value & ~(PAGE - 1)


def page_up(value):
    return (value + PAGE - 1) & ~(PAGE - 1)


def uleb(value):
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        out.append(byte | (0x80 if value else 0))
        if not value:
            return bytes(out)


class ExtractError(Exception):
    pass


class Output:
    """The new image's segments as writable memory at their cache addresses."""

    def __init__(self):
        self.segments = []  # [Segment with .data bytearray]

    def add(self, segment, data):
        segment.data = data
        self.segments.append(segment)
        self.segments.sort(key=lambda s: s.vmaddr)

    def segment_at(self, address):
        return next((s for s in self.segments if s.vmaddr <= address < s.vmaddr + len(s.data)), None)

    def read(self, address, size):
        seg = self.segment_at(address)
        if seg is None or address + size > seg.vmaddr + len(seg.data):
            raise LookupError(f'{address:#x} is outside the new image')
        at = address - seg.vmaddr
        return bytes(seg.data[at:at + size])

    def write(self, address, data):
        seg = self.segment_at(address)
        if seg is None or address + len(data) > seg.vmaddr + len(seg.data):
            raise LookupError(f'{address:#x} is outside the new image')
        at = address - seg.vmaddr
        seg.data[at:at + len(data)] = data

    def u32(self, address):
        return struct.unpack('<I', self.read(address, 4))[0]

    def u64(self, address):
        return struct.unpack('<Q', self.read(address, 8))[0]


class Extraction:
    def __init__(self, cache, path, install_name=None):
        self.cache = cache
        self.image = cache.image(path)
        if self.image.cputype != 0x01000007:
            raise ExtractError(f'{path}: not an x86_64 image')
        if cache.selector_base is None:
            raise ExtractError(f'ObjC optimization header version {cache.objc_version} is not supported')
        self.install_name = install_name or path
        self.out = Output()
        self.fixups = {}      # address -> ('rebase', target) | ('bind', ordinal, name, addend, weak)
        self.problems = []    # unresolved items; extraction fails if any remain
        self.stats = {}
        self.ranges = [(s.vmaddr, s.end) for s in self.image.segments if s.name != '__LINKEDIT']
        self.dependencies = [(cmd, p) for cmd, p in self.image.dylibs]
        self._providers = None

    # ----- helpers
    def own(self, address):
        return any(start <= address < end for start, end in self.ranges)

    def note(self, key, count=1):
        self.stats[key] = self.stats.get(key, 0) + count

    def problem(self, text):
        self.problems.append(text)

    # ----- layout
    def build_segments(self):
        for seg in self.image.segments:
            if seg.name == '__LINKEDIT':
                continue
            start, end = page_down(seg.vmaddr), page_up(seg.end)
            data = bytearray(end - start)
            data[seg.vmaddr - start:seg.end - start] = self.cache.read(seg.vmaddr, seg.vmsize)
            new = dsc.Segment(seg.pack())
            new.vmaddr, new.vmsize = start, end - start
            if seg.name == '__TEXT':
                new.initprot = new.maxprot = VM_PROT_READ | VM_PROT_EXECUTE
            else:
                new.initprot = new.maxprot = VM_PROT_READ | VM_PROT_WRITE
                if seg.name == '__DATA_CONST':
                    new.flags |= SG_READ_ONLY
            self.out.add(new, data)
        for a, b in zip(self.out.segments, self.out.segments[1:]):
            if a.vmaddr + len(a.data) > b.vmaddr:
                raise ExtractError(f'segments {a.name} and {b.name} share a page')

    # ----- strings
    def own_strings(self):
        """{bytes: address} of every NUL-terminated string in the image's string sections (first occurrence)."""
        if not hasattr(self, '_strings'):
            table = {}
            for name in STRING_SECTIONS:
                for sect in self.image.sections():
                    if sect.name != name:
                        continue
                    data = self.cache.read(sect.addr, sect.size)
                    at = 0
                    while at < len(data):
                        end = data.find(b'\0', at)
                        if end < 0:
                            break
                        table.setdefault((name, data[at:end]), sect.addr + at)
                        at = end + 1
            self._strings = table
        return self._strings

    def own_string(self, text, prefer=STRING_SECTIONS):
        strings = self.own_strings()
        for name in prefer + tuple(n for n in STRING_SECTIONS if n not in prefer):
            if (name, text) in strings:
                return strings[(name, text)]
        return None

    def foreign_string(self, target):
        """TARGET's string if it lies in a string section of another cache image, else None."""
        image = self.cache.image_at(target)
        if image is None:
            return None
        for sect in image.sections():
            if sect.name in STRING_SECTIONS and sect.addr <= target < sect.end:
                return sect.name, self.cache.cstring(target)
        return None

    # ----- exported symbols
    def providers(self):
        """{(defining image path, symbol): (ordinal, symbol)} for every symbol reachable through a dependency,
        following LC_REEXPORT_DYLIB chains and re-exported symbols."""
        if self._providers is None:
            table = {}
            # a dependency's own exports first, so a symbol binds to the library that defines it when that library
            # is a direct dependency, and only then the libraries reached through re-exports
            for depth in (0, 1):
                for ordinal, (cmd, path) in enumerate(self.dependencies, 1):
                    self._reach(table, ordinal, path, depth)
            self._providers = table
        return self._providers

    def _reach(self, table, ordinal, path, depth):
        """Add what dependency ORDINAL (PATH) provides: its own exports (DEPTH 0) or everything it re-exports too."""
        seen, stack = set(), [path]
        while stack:
            current = stack.pop()
            if current in seen:
                continue
            seen.add(current)
            try:
                image = self.cache.image(current)
            except LookupError:
                continue
            for name, (flags, value) in image.exports().items():
                if flags & dsc.EXPORT_SYMBOL_FLAGS_REEXPORT:
                    sub_ordinal, sub_name = value
                    if 0 < sub_ordinal <= len(image.dylibs):
                        table.setdefault((image.dylibs[sub_ordinal - 1][1], sub_name), (ordinal, name))
                else:
                    table.setdefault((current, name), (ordinal, name))
            if depth:
                stack.extend(p for c, p in image.dylibs if c == dsc.LC_REEXPORT_DYLIB)

    def symbols_at(self, image):
        """{address: [names]} of IMAGE's regular exports."""
        cached = getattr(image, '_by_address', None)
        if cached is None:
            cached = {}
            for name, (flags, value) in image.exports().items():
                if not flags & (dsc.EXPORT_SYMBOL_FLAGS_REEXPORT | dsc.EXPORT_SYMBOL_FLAGS_STUB_AND_RESOLVER):
                    cached.setdefault(image.address + value, []).append(name)
                elif flags & dsc.EXPORT_SYMBOL_FLAGS_STUB_AND_RESOLVER:
                    cached.setdefault(image.address + value[0], []).append(name)
            image._by_address = cached
            image._sorted = sorted(cached)
        return cached

    def resolve(self, target):
        """('bind', ordinal, name, addend) for a pointer to another image's exported symbol (or a small offset into
        one), or None."""
        image = self.cache.image_at(target)
        if image is None:
            return None
        table = self.symbols_at(image)
        providers = self.providers()
        candidates = [(target, 0)]
        if target not in table:
            import bisect
            i = bisect.bisect_right(image._sorted, target) - 1
            if i >= 0 and target - image._sorted[i] <= 0x40:
                candidates.append((image._sorted[i], target - image._sorted[i]))
        for address, addend in candidates:
            for name in sorted(table.get(address, ())):
                hit = providers.get((image.path, name))
                if hit:
                    return ('bind', hit[0], hit[1], addend)
        return None

    def weak_ordinal(self, ordinal):
        return self.dependencies[ordinal - 1][0] == dsc.LC_LOAD_WEAK_DYLIB

    # ----- Objective-C
    def fix_image_info(self):
        sect = self.image.section('__objc_imageinfo')
        if sect is None:
            return
        version, flags = struct.unpack('<II', self.out.read(sect.addr, 8))
        self.out.write(sect.addr, struct.pack('<II', version, flags & ~IMAGE_INFO_CLEAR))
        self.stats['objc_image_info_flags'] = f'{flags:#x} -> {flags & ~IMAGE_INFO_CLEAR:#x}'

    def selrefs(self):
        """Point every selector reference at the image's own copy of the name; {name: selref address}."""
        found = {}
        sect = self.image.section('__objc_selrefs')
        if sect is None:
            return found
        for at in range(sect.addr, sect.end, 8):
            target = self.cache.pointer(at)
            name = self.cache.cstring(target)
            own = target if self.own(target) else self.own_string(name, ('__objc_methname',))
            if own is None:
                self.problem(f'selector {name.decode()} has no copy in the image')
                continue
            self.fixups[at] = ('rebase', own)
            found.setdefault(name, at)
        self.note('selector_refs', len(found))
        return found

    def own_protocols(self):
        if not hasattr(self, '_protocols'):
            table = {}
            sect = self.image.section('__objc_protolist')
            for at in range(sect.addr, sect.end, 8) if sect else ():
                proto = self.cache.pointer(at)
                if not self.own(proto):
                    self.problem(f'__objc_protolist entry {at:#x} points outside the image')
                    continue
                table.setdefault(self.cache.cstring(self.cache.pointer(proto + 8)), proto)
            self._protocols = table
        return self._protocols

    def own_protocol(self, at):
        """Rebase the protocol pointer at AT onto the image's own protocol of the same name."""
        target = self.cache.pointer(at)
        if target == 0:
            return
        if self.own(target):
            self.fixups[at] = ('rebase', target)
            return
        name = self.cache.cstring(self.cache.pointer(target + 8))
        own = self.own_protocols().get(name)
        if own is None:
            self.problem(f'protocol {name.decode()} referenced at {at:#x} has no definition in the image')
            return
        self.fixups[at] = ('rebase', own)
        self.note('protocol_refs_ununiqued')

    def protocol_list(self, address):
        if not address:
            return
        count = self.cache.u64(address)
        for i in range(count):
            self.own_protocol(address + 8 + i*8)

    def method_list(self, address, selrefs, kind):
        if not address:
            return
        if address & 1:
            self.problem(f'{kind} method list {address:#x} is a relative list of lists (preattached categories)')
            return
        if not self.own(address):
            self.problem(f'{kind} method list {address:#x} lies outside the image')
            return
        if address in self._seen_lists:
            return
        self._seen_lists.add(address)
        flags_entsize, count = struct.unpack('<II', self.cache.read(address, 8))
        entsize = flags_entsize & 0xFFFC
        if flags_entsize & METHOD_LIST_RELATIVE:
            if entsize != 12:
                self.problem(f'relative method list {address:#x} entsize {entsize}')
                return
            for i in range(count):
                entry = address + 8 + i*12
                name_off, types_off, imp_off = struct.unpack('<iii', self.cache.read(entry, 12))
                if flags_entsize & METHOD_LIST_DIRECT_SELECTORS:
                    name = self.cache.cstring(self.cache.selector_base + name_off)
                else:
                    name = self.cache.cstring(self.cache.pointer(entry + name_off))
                selref = selrefs.get(name)
                if selref is None:
                    self.problem(f'method {name.decode()} ({kind}) has no selector reference in the image')
                    continue
                types = entry + 4 + types_off
                if not self.own(types):
                    own = self.own_string(self.cache.cstring(types), ('__objc_methtype',))
                    if own is None:
                        self.problem(f'method {name.decode()} types {types:#x} have no copy in the image')
                        continue
                    types_off = own - (entry + 4)
                    self.note('method_types_ununiqued')
                if imp_off and not self.own(entry + 8 + imp_off):
                    self.problem(f'method {name.decode()} ({kind}) implementation lies outside the image')
                self.out.write(entry, struct.pack('<ii', selref - entry, types_off))
            self.note('relative_methods', count)
        else:
            if entsize != 24:
                self.problem(f'method list {address:#x} entsize {entsize}')
                return
            self.note('pointer_methods', count)  # names and types are pointers: the generic pass handles them
        flags = flags_entsize & ~(METHOD_LIST_DIRECT_SELECTORS | METHOD_LIST_FIXED_UP)
        self.out.write(address, struct.pack('<I', flags))

    def own_list(self, field):
        """A class_ro list field the cache builder turned into a relative list of lists (low bit set: the class's
        own list plus categories of other images, preattached) points back at the class's own list."""
        value = self.cache.pointer(field)
        if not value & 1:
            return
        lists = value - 1
        entsize, count = struct.unpack('<II', self.cache.read(lists, 8))
        if entsize & 0xFFFF != 8:
            self.problem(f'relative list of lists {lists:#x} entsize {entsize:#x}')
            return
        own = []
        for i in range(count):
            entry = lists + 8 + i*8
            packed, = struct.unpack('<q', self.cache.read(entry, 8))
            target = entry + (packed >> 16)
            if self.own(target):
                own.append(target)
            else:
                self.note('preattached_foreign_lists_dropped')
        if len(own) != 1:
            self.problem(f'relative list of lists {lists:#x} has {len(own)} lists of this image')
            return
        self.fixups[field] = ('rebase', own[0])
        self.note('preattached_lists_restored')

    def list_at(self, field):
        fix = self.fixups.get(field)
        return fix[1] if fix and fix[0] == 'rebase' else self.cache.pointer(field)

    def objc(self):
        self.fix_image_info()
        selrefs = self.selrefs()
        self._seen_lists = set()
        ptr = self.cache.pointer
        sect = self.image.section('__objc_protorefs')
        for at in range(sect.addr, sect.end, 8) if sect else ():
            self.own_protocol(at)
        self.own_protocols()
        empty = None
        sect = self.image.section('__objc_classlist')
        classes = [ptr(at) for at in range(sect.addr, sect.end, 8)] if sect else []
        for cls in classes:
            if not self.own(cls):
                self.problem(f'class list entry {cls:#x} lies outside the image')
                continue
            for klass in (cls, ptr(cls)):
                if not self.own(klass):
                    self.problem(f'metaclass of {cls:#x} lies outside the image')
                    continue
                cache_field = ptr(klass + 16)
                if empty is None:
                    empty = self.resolve(cache_field)
                    if empty is None or empty[2] != EMPTY_CACHE:
                        empty = None
                        found = next(((o, n) for (path, n), (o, _) in self.providers().items()
                                      if n == EMPTY_CACHE), None)
                        if found:
                            empty = ('bind', found[0], EMPTY_CACHE, 0)
                if empty is None:
                    self.problem('no __objc_empty_cache export reachable')
                else:
                    self.fixups[klass + 16] = empty
                if self.out.u64(klass + 24):
                    self.note('preoptimized_caches_cleared')
                self.out.write(klass + 24, b'\0'*8)
                self.fixups.pop(klass + 24, None)
                ro = ptr(klass + 32) & ~0x7
                if not self.own(ro):
                    self.problem(f'class_ro of {klass:#x} lies outside the image')
                    continue
                for offset in (32, 40, 64):
                    self.own_list(ro + offset)
                self.method_list(self.list_at(ro + 32), selrefs, 'class')
                self.protocol_list(self.list_at(ro + 40))
            self.note('classes')
        sect = self.image.section('__objc_catlist')
        for at in range(sect.addr, sect.end, 8) if sect else ():
            cat = ptr(at)
            self.method_list(ptr(cat + 16), selrefs, 'category')
            self.method_list(ptr(cat + 24), selrefs, 'category')
            self.protocol_list(ptr(cat + 32))
            self.note('categories')
        for proto in sorted(set(self.own_protocols().values())):
            for offset in (24, 32, 40, 48):
                self.method_list(ptr(proto + offset), selrefs, 'protocol')
            self.protocol_list(ptr(proto + 16))
            size, flags = struct.unpack('<II', self.out.read(proto + 64, 8))
            if flags & PROTOCOL_CLEAR:
                self.out.write(proto + 68, struct.pack('<I', flags & ~PROTOCOL_CLEAR))
                self.note('protocol_flags_cleared')
            self.note('protocols')

    # ----- pointers
    def pointers(self):
        """Every pointer slide info rebases inside the image's own segments: rebase, bind or un-unique it."""
        for start, end in self.ranges:
            for at, target in sorted(self.cache.rebases(start, end).items()):
                if at in self.fixups:
                    continue
                self.pointer_fixup(at, target, 'data')

    def pointer_fixup(self, at, target, where):
        if target == 0:
            self.out.write(at, b'\0'*8)
            self.note(f'{where}_null')
            return True
        if self.own(target) or (self.got and self.got[0] <= target < self.got[1]):
            self.fixups[at] = ('rebase', target)
            self.note(f'{where}_rebases')
            return True
        bind = self.resolve(target)
        if bind:
            self.fixups[at] = bind
            self.note(f'{where}_binds')
            return True
        string = self.foreign_string(target)
        if string:
            own = self.own_string(string[1], (string[0],))
            if own is not None:
                self.fixups[at] = ('rebase', own)
                self.note(f'{where}_strings_ununiqued')
                return True
        return False

    # ----- uniqued GOT
    def got_region(self):
        """[start, end) of the uniqued GOT slots in the DATA_CONST mapping holding the image's data: the bytes of
        that mapping no image owns, before its first image."""
        data_const = next(s for s in self.image.segments if s.name == '__DATA_CONST')
        mapping = self.cache.locate(data_const.vmaddr)
        first = min(s.vmaddr for _, path in self.cache.image_list for s in self.cache.image(path).segments
                    if s.name != '__LINKEDIT' and mapping[0] <= s.vmaddr < mapping[1])
        return mapping[0], first

    def scan_relative(self):
        """Candidate GOT slots: every 4-byte value in the image's code and data that, read as a RIP-relative
        displacement (ends 4 to 8 bytes before the next instruction) or a 32-bit relative pointer, lands on an
        8-byte slot of the uniqued GOT region."""
        lo, hi = self.got
        found = set()
        for seg in self.image.segments:
            if seg.name == '__LINKEDIT':
                continue
            data = self.cache.read(seg.vmaddr, seg.vmsize)
            code = seg.name == '__TEXT'
            for i in range(0, len(data) - 3):
                disp, = struct.unpack_from('<i', data, i)
                base = seg.vmaddr + i
                for extra in ((4, 5, 6, 8) if code else (0,)):
                    target = base + extra + disp
                    if lo <= target < hi and not (target - lo) % 8:
                        found.add(target)
                    target = base + disp
                    if lo <= target & ~1 < hi and not ((target & ~1) - lo) % 8:
                        found.add(target & ~1)
        return found

    def got_slots(self):
        self.got = self.got_region()
        self.stats['uniqued_got_region'] = [f'{self.got[0]:#x}', f'{self.got[1]:#x}']
        candidates = self.scan_relative()
        bound = []
        for slot in sorted(candidates):
            target = self.cache.pointer(slot)
            if self.pointer_fixup(slot, target, 'got'):
                bound.append(slot)
        self.stats['got_candidates'] = len(candidates)
        self.stats['got_slots_bound'] = len(bound)
        if bound:
            start, end = page_down(min(bound)), page_up(max(bound) + 8)
            seg = dsc.Segment(struct.pack('<II', dsc.LC_SEGMENT_64, 72) + b'\0'*64)
            seg.name, seg.vmaddr, seg.vmsize = GOT_SEGMENT, start, end - start
            seg.initprot = seg.maxprot = VM_PROT_READ | VM_PROT_WRITE
            seg.flags = SG_READ_ONLY
            # one section over the whole segment: tools (dyld_info, llvm-objdump) require fixups inside a section
            sect = dsc.Section(b'\0'*80)
            sect.name, sect.segment, sect.addr, sect.size, sect.align = '__got', GOT_SEGMENT, start, end - start, 3
            seg.sections = [sect]
            self.out.add(seg, bytearray(end - start))
            for slot in list(self.fixups):
                if start <= slot < end:
                    self.out.write(slot, b'\0'*8)
            self.got = (start, end)
        # slots outside the chosen pages, or unresolved, stay unbound
        for slot in sorted(candidates):
            if slot not in self.fixups:
                self.note('got_candidates_unresolved')

    # ----- linkedit
    def exports_trie(self):
        entries = {}
        for name, (flags, value) in self.image.exports().items():
            entries[name] = (flags, value)
        return build_trie(entries)

    def build(self):
        self.build_segments()
        self.got = None
        self.objc()
        self.got_slots()
        self.pointers()
        for at, fix in self.fixups.items():
            if fix[0] == 'rebase':
                self.out.write(at, struct.pack('<Q', fix[1]))
            else:
                self.out.write(at, b'\0'*8)
        unresolved = [at for start, end in self.ranges for at in self.cache.rebases(start, end)
                      if at not in self.fixups and self.cache.pointer(at)]
        for at in unresolved:
            target = self.cache.pointer(at)
            image = self.cache.image_at(target)
            self.problem(f'pointer at {at:#x} -> {target:#x} ({image.path if image else "no image"}) unresolved')
        if self.problems:
            raise ExtractError('\n'.join(self.problems[:200]) + (f'\n... {len(self.problems)} problems'
                                                                if len(self.problems) > 200 else ''))
        return self.write()

    def write(self):
        img = self.image
        segments = [s for s in self.out.segments]
        # file layout: segments in address order, page-aligned
        offset = 0
        for seg in segments:
            seg.fileoff = offset
            seg.filesize = len(seg.data)
            offset += seg.filesize
        # sections keep their addresses; file offsets follow the segment
        section_index, index = {}, 1
        for seg in segments:
            for sect in seg.sections:
                if sect.flags & 0xFF == S_ZEROFILL:
                    sect.offset = 0
                else:
                    sect.offset = seg.fileoff + sect.addr - seg.vmaddr
                if sect.name == '__stubs':
                    sect.reserved1 = 0
                elif sect.flags & 0xFF != S_SYMBOL_STUBS:
                    sect.reserved1 = 0
                section_index[(seg.name, sect.name, sect.addr)] = index
                index += 1
        seg_index = {id(s): i for i, s in enumerate(segments)}
        linkedit = bytearray()

        def align8():
            linkedit.extend(b'\0' * (-len(linkedit) % 8))
        # rebases
        rebase = bytearray([0x11])  # REBASE_OPCODE_SET_TYPE_IMM | POINTER
        rebases = sorted(a for a, f in self.fixups.items() if f[0] == 'rebase')
        for at in rebases:
            seg = self.out.segment_at(at)
            rebase += bytes([0x20 | seg_index[id(seg)]]) + uleb(at - seg.vmaddr) + bytes([0x51])
        rebase += b'\0'
        rebase_off = len(linkedit)
        linkedit += rebase
        align8()
        # binds
        bind = bytearray([0x51])  # BIND_OPCODE_SET_TYPE_IMM | POINTER
        binds = sorted((f[1], f[2], f[3], a) for a, f in self.fixups.items() if f[0] == 'bind')
        state = None
        for ordinal, name, addend, at in binds:
            weak = self.weak_ordinal(ordinal)
            if state != (ordinal, name, addend):
                bind += bytes([0x20]) + uleb(ordinal) if ordinal > 15 else bytes([0x10 | ordinal])
                bind += bytes([0x40 | (1 if weak else 0)]) + name.encode() + b'\0'
                bind += bytes([0x60]) + sleb(addend)
                state = (ordinal, name, addend)
            seg = self.out.segment_at(at)
            bind += bytes([0x70 | seg_index[id(seg)]]) + uleb(at - seg.vmaddr) + bytes([0x90])
        bind += b'\0'
        bind_off = len(linkedit)
        linkedit += bind
        align8()
        export_off = len(linkedit)
        trie = self.exports_trie()
        linkedit += trie
        align8()
        # function starts and data in code, copied
        extra_cmds = []
        for kind in (dsc.LC_FUNCTION_STARTS, dsc.LC_DATA_IN_CODE):
            body = img.command(kind)
            if body is None:
                continue
            off, size = struct.unpack_from('<II', body, 8)
            at = len(linkedit)
            linkedit += img.linkedit(off, size)
            align8()
            extra_cmds.append((kind, at, size))
        # symbols: regular exports, for tools and crash reports
        symbols, strings = bytearray(), bytearray(b' \0')
        regular = sorted((img.address + v, n) for n, (f, v) in img.exports().items()
                         if not f & (dsc.EXPORT_SYMBOL_FLAGS_REEXPORT | dsc.EXPORT_SYMBOL_FLAGS_STUB_AND_RESOLVER))
        nsyms = 0
        for address, name in sorted(regular, key=lambda x: x[1]):
            seg = self.out.segment_at(address)
            sect = next((s for s in seg.sections if s.addr <= address < s.end), None) if seg else None
            if sect is None:
                continue
            symbols += struct.pack('<IBBHQ', len(strings), N_SECT | N_EXT,
                                   section_index[(seg.name, sect.name, sect.addr)], 0, address)
            strings += name.encode() + b'\0'
            nsyms += 1
        symoff = len(linkedit)
        linkedit += symbols
        stubs = img.section('__stubs')
        nindirect = stubs.size // max(stubs.reserved2, 1) if stubs else 0
        indirect_off = len(linkedit)
        linkedit += struct.pack('<I', INDIRECT_SYMBOL_ABS) * nindirect
        str_off = len(linkedit)
        linkedit += strings
        linkedit.extend(b'\0' * (-len(linkedit) % 8))
        link_start = page_up(offset)
        link_vm = page_up(segments[-1].vmaddr + segments[-1].vmsize)
        link_seg = dsc.Segment(struct.pack('<II', dsc.LC_SEGMENT_64, 72) + b'\0'*64)
        link_seg.name, link_seg.vmaddr, link_seg.vmsize = '__LINKEDIT', link_vm, page_up(len(linkedit))
        link_seg.fileoff, link_seg.filesize = link_start, len(linkedit)
        link_seg.initprot = link_seg.maxprot = VM_PROT_READ

        def lo(x):
            return link_start + x
        commands = [s.pack() for s in segments] + [link_seg.pack()]
        name = self.install_name.encode() + b'\0'
        id_body = img.command(dsc.LC_ID_DYLIB)
        _, _, _, timestamp, current, compat = struct.unpack_from('<IIIIII', id_body)
        id_cmd = struct.pack('<IIIIII', dsc.LC_ID_DYLIB, 0, 24, timestamp, current, compat) + name
        id_cmd += b'\0' * (-len(id_cmd) % 8)
        commands.append(id_cmd[:4] + struct.pack('<I', len(id_cmd)) + id_cmd[8:])
        commands.append(struct.pack('<12I', dsc.LC_DYLD_INFO_ONLY, 48, lo(rebase_off), len(rebase), lo(bind_off),
                                    len(bind), 0, 0, 0, 0, lo(export_off), len(trie)))
        commands.append(struct.pack('<6I', dsc.LC_SYMTAB, 24, lo(symoff), nsyms, lo(str_off), len(strings)))
        commands.append(struct.pack('<20I', dsc.LC_DYSYMTAB, 80, 0, 0, 0, nsyms, nsyms, 0, 0, 0, 0, 0, 0, 0,
                                    lo(indirect_off) if nindirect else 0, nindirect, 0, 0, 0, 0))
        uuid = hashlib.sha256(b'cache_dylib\0' + img.command(dsc.LC_UUID)[8:24] +
                              self.install_name.encode()).digest()[:16]
        commands.append(struct.pack('<II', dsc.LC_UUID, 24) + uuid)
        for cmd, body in img.commands:
            if cmd in (dsc.LC_BUILD_VERSION, dsc.LC_SOURCE_VERSION) or cmd in dsc.DYLIB_COMMANDS:
                commands.append(body)
        for kind, at, size in extra_cmds:
            commands.append(struct.pack('<IIII', kind, 16, lo(at), size))
        header_room = min(s.addr for s in segments[0].sections) - segments[0].vmaddr - 32
        sizeofcmds = sum(len(c) for c in commands)
        self.stats['header_room'] = header_room - sizeofcmds
        if sizeofcmds + 16 > header_room:  # 16: LC_CODE_SIGNATURE added by codesign
            raise ExtractError(f'load commands need {sizeofcmds + 16} bytes, the header has {header_room}')
        text = segments[0]
        if text.name != '__TEXT' or text.vmaddr != img.address:
            raise ExtractError('__TEXT must come first and start at the Mach-O header')
        flags = img.flags & ~MH_DYLIB_IN_CACHE
        header = struct.pack('<IiiIIIII', dsc.MH_MAGIC_64, img.cputype, 3, img.filetype, len(commands), sizeofcmds,
                             flags, 0) + b''.join(commands)
        text.data[:len(header)] = header
        text.data[len(header):header_room + 32] = b'\0' * (header_room + 32 - len(header))
        blob = bytearray()
        for seg in segments:
            blob += seg.data
        blob += b'\0' * (link_start - len(blob))
        blob += linkedit
        self.stats.update(rebases=len(rebases), binds=len(binds), exports=len(img.exports()),
                          segments={s.name + f'@{s.vmaddr:#x}': s.vmsize for s in segments + [link_seg]})
        return bytes(blob)

    def report(self):
        binds = {}
        for at, fix in self.fixups.items():
            if fix[0] == 'bind':
                path = self.dependencies[fix[1] - 1][1]
                binds.setdefault(path, set()).add(fix[2])
        return dict(image=self.image.path, install_name=self.install_name, stats=self.stats,
                    dependencies=[dict(path=p, weak=c == dsc.LC_LOAD_WEAK_DYLIB, reexport=c == dsc.LC_REEXPORT_DYLIB)
                                  for c, p in self.dependencies],
                    binds={p: sorted(n) for p, n in sorted(binds.items())})


def sleb(value):
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        done = (value == 0 and not byte & 0x40) or (value == -1 and byte & 0x40)
        out.append(byte | (0 if done else 0x80))
        if done:
            return bytes(out)


def build_trie(entries):
    """An export trie for ENTRIES {name: (flags, value)} (value as parse_trie returns it)."""
    root = {}
    for name in entries:
        node = root
        for ch in name.encode():
            node = node.setdefault(ch, {})
        node[None] = entries[name]

    # compress into edges: node = (terminal or None, [(label bytes, child)])
    def compress(node):
        terminal = node.get(None)
        children = []
        for ch in sorted(k for k in node if k is not None):
            label, child = bytes([ch]), node[ch]
            while None not in child and len(child) == 1:
                (next_ch, grand), = child.items()
                label += bytes([next_ch])
                child = grand
            children.append((label, compress(child)))
        return [terminal, children, 0]
    tree = compress(root)
    order = []

    def walk(node):
        order.append(node)
        for _, child in node[1]:
            walk(child)
    walk(tree)

    def terminal_bytes(info):
        if info is None:
            return b''
        flags, value = info
        if flags & dsc.EXPORT_SYMBOL_FLAGS_REEXPORT:
            ordinal, name = value
            return uleb(flags) + uleb(ordinal) + name.encode() + b'\0'
        if flags & dsc.EXPORT_SYMBOL_FLAGS_STUB_AND_RESOLVER:
            return uleb(flags) + uleb(value[0]) + uleb(value[1])
        return uleb(flags) + uleb(value)
    while True:
        offset, changed = 0, False
        for node in order:
            if node[2] != offset:
                node[2], changed = offset, True
            term = terminal_bytes(node[0])
            size = len(uleb(len(term))) + len(term) + 1
            for label, child in node[1]:
                size += len(label) + 1 + len(uleb(child[2]))
            offset += size
        if not changed:
            break
    out = bytearray()
    for node in order:
        term = terminal_bytes(node[0])
        out += uleb(len(term)) + term + bytes([len(node[1])])
        for label, child in node[1]:
            out += label + b'\0' + uleb(child[2])
    return bytes(out)


def extract(cache_dir, arch, path, out, install_name=None):
    cache = dsc.Cache(cache_dir, arch)
    try:
        job = Extraction(cache, path, install_name)
        data = job.build()
        Path(out).write_bytes(data)
        Path(out).chmod(0o755)
        record = job.report()
        record['cache'] = dict(directory=str(Path(cache_dir).resolve()), arch=arch, uuid=cache.uuid.hex(),
                               subcaches=cache.subcaches)
        record['sha256_unsigned'] = hashlib.sha256(data).hexdigest()
        return record
    finally:
        cache.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command', required=True)
    e = sub.add_parser('extract')
    e.add_argument('cache_dir')
    e.add_argument('arch')
    e.add_argument('image')
    e.add_argument('out')
    e.add_argument('--install-name')
    e.add_argument('--report')
    args = parser.parse_args(argv)
    try:
        record = extract(args.cache_dir, args.arch, args.image, args.out, args.install_name)
    except ExtractError as error:
        sys.exit(f'MISSING/unsupported: {error}')
    text = json.dumps(record, indent=2) + '\n'
    if args.report:
        Path(args.report).write_text(text)
    print(json.dumps(record['stats'], indent=2))


if __name__ == '__main__':
    main()
