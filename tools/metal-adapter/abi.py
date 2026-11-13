#!/usr/bin/env python3
"""abi.py: the private descriptor structs OCLP's HD 4000 driver reads by offset, MetalOld's layout against 27's.

  abi.py layouts METALOLD METAL27 [--out OUT/work/layouts.inc]  compare, and write the shim's translation table
  abi.py show METALOLD METAL27 STRUCT                         both layouts of one struct, field by field

The driver (11.7.10, built against MetalOld's Metal 306) asks a descriptor for its private struct
(`-_descriptorPrivate` / `-descriptorPrivate`) and reads fields at MetalOld's offsets. Each class's `_private` ivar
carries the whole struct in its ObjC type encoding, field names included, so both layouts are computed from the
binaries' own metadata (`otool -ov`); nothing is guessed. A struct whose MetalOld fields all keep their place in 27
(only appended fields) needs nothing. For the others the table maps every MetalOld field to the same-named field in
27 (bytes, or bits of a bitfield); fields 27 dropped are left zero. The shim (src/shim.c) returns a MetalOld-layout
copy to callers inside the driver image only, and refuses to translate if 27's encoding is not the one in the table.

Bitfields: the encoding drops a bitfield's declared type. These runs are 32-bit units (clang starts a field that would
cross a 32-bit boundary at the next one): 27's getters read logicOp at bit 32, not 29, and the colour attachment's
writeMask at bit 32, not 28. tests/test_abi.py checks computed positions against the getters' shifts and masks.
Two 27 fields grew from 1 to 2 bits: blendingEnabled (27's getter: == 1) and alphaToCoverage/alphaToOneEnabled
(27's getters: nonzero); CONVERT gives the MetalOld bit the same meaning.
"""
import argparse
import re
import subprocess
import sys

SIZES = {'c': 1, 'C': 1, 'B': 1, 's': 2, 'S': 2, 'i': 4, 'I': 4, 'l': 4, 'L': 4, 'f': 4, 'q': 8, 'Q': 8, 'd': 8,
         '*': 8, '#': 8, ':': 8, '@': 8, '^': 8, 'v': 0}
SELECTORS = ('_descriptorPrivate', 'descriptorPrivate')
# the structs the driver reads through those selectors (its call sites: render pipeline state, blend/PS/WM/TE/VF state,
# vertex function key, sampler, texture inits and fillTextureData, heap, render pass and its attachments)
DRIVER_READS = {'MTLRenderPipelineDescriptorPrivate', 'MTLRenderPipelineAttachmentDescriptorPrivate',
                'MTLTextureDescriptorPrivate', 'MTLSamplerDescriptorPrivate', 'MTLHeapDescriptorPrivate',
                'MTLRenderPassDescriptorPrivate', 'MTLRenderPassAttachmentDescriptorPrivate'}
CONVERT = {'blendingEnabled': 2, 'alphaToCoverageEnabled': 1, 'alphaToOneEnabled': 1}   # 1: nonzero, 2: == 1


class Enc:
    """Parse an ObjC type encoding (as otool -ov prints an ivar's type) into a tree."""
    def __init__(self, s):
        self.s, self.i = s, 0

    def name(self):
        if self.i < len(self.s) and self.s[self.i] == '"':
            j = self.s.index('"', self.i + 1)
            n, self.i = self.s[self.i + 1:j], j + 1
            return n
        return None

    def type(self):
        s, c = self.s, self.s[self.i]
        self.i += 1
        if c == '@':
            if self.i < len(s) and s[self.i] == '"' and self.i + 1 < len(s) and s[self.i + 1] in '<ABCDEFGHIJKLMNOPQRSTUVWXYZ_':
                # @"ClassName" or @"<Protocol>": the quoted class belongs to the type, a field name never starts so
                j = s.index('"', self.i + 1)
                self.i = j + 1
            return ('scalar', 8, 8)
        if c == '^':
            if s[self.i] == '?':
                self.i += 1
            else:
                self.type()
            return ('scalar', 8, 8)
        if c == 'b':
            m = re.match(r'\d+', s[self.i:])
            self.i += m.end()
            return ('bits', int(m.group()))
        if c == '[':
            m = re.match(r'\d+', s[self.i:])
            self.i += m.end()
            t = self.type()
            assert s[self.i] == ']', s[self.i:self.i + 20]
            self.i += 1
            size, align = layout_of(t)
            return ('array', int(m.group()) * size, align, t)
        if c in '{(':
            close = '}' if c == '{' else ')'
            j = s.index('=', self.i)
            tag = s[self.i:j]
            self.i = j + 1
            fields = []
            while s[self.i] != close:
                n = self.name()
                fields.append((n, self.type()))
            self.i += 1
            return ('struct' if c == '{' else 'union', tag, fields)
        if c in SIZES:
            return ('scalar', SIZES[c], SIZES[c])
        raise ValueError(f'type {c!r} at {self.i}: {s[self.i - 10:self.i + 20]}')


def layout_of(t):
    """(size, alignment) of a parsed type."""
    if t[0] in ('scalar',):
        return t[1], t[2]
    if t[0] == 'array':
        return t[1], t[2]
    leaves = []
    size = place(t, 0, '', leaves)
    align = max([a for _, a in aligns(t)] or [1])
    return size, align


def aligns(t):
    if t[0] == 'scalar':
        yield t[1], t[2]
    elif t[0] == 'array':
        yield t[1], t[2]
    elif t[0] in ('struct', 'union'):
        for _, ft in t[2]:
            if ft[0] != 'bits':
                yield from aligns(ft)
            else:
                yield 8, 8


def place(t, base, path, out):
    """Lay out struct/union T at byte offset BASE; append leaves (path, offset, size, bit, bits). Returns the size."""
    kind, _, fields = t
    off, end, bit = 0, 0, None   # bit: position (in bits from the run start offset) inside a bitfield run
    run_start = 0
    for n, ft in fields:
        p = f'{path}.{n}' if path and n else (n or path)
        at = 0 if kind == 'union' else off
        if ft[0] == 'bits':
            if kind == 'union':
                raise ValueError('bitfield directly in a union')
            if bit is None:
                run_start, bit = off, 0
            if bit % 32 + ft[1] > 32:
                bit = (bit + 31) // 32 * 32
            out.append((p, base + run_start, 0, bit, ft[1]))
            bit += ft[1]
            off = run_start + (bit + 7) // 8
            end = max(end, off)
            continue
        if bit is not None:
            bit = None
        size, align = layout_of(ft)
        if kind == 'struct':
            at = (off + align - 1) // align * align
        if ft[0] in ('struct', 'union'):
            place(ft, base + at, p, out)
        else:
            out.append((p, base + at, size, 0, 0))
        if kind == 'struct':
            off = at + size
        end = max(end, at + size)
    align = max([a for _, a in aligns(t)] or [1])
    return (end + align - 1) // align * align


def struct_layout(enc):
    t = Enc(enc).type()
    leaves = []
    size = place(t, 0, '', leaves)
    return t[1], size, leaves


def classes(macho):
    """{class: {'private': encoding, 'selectors': set}} for classes with a struct _private and a descriptorPrivate."""
    out = subprocess.run(['otool', '-ov', macho], capture_output=True, text=True, check=True).stdout
    res, cur, sect, pend = {}, None, None, {}
    for l in out.splitlines():
        s = l.strip()
        if re.match(r'^[0-9a-f]{16} 0x[0-9a-f]+', l) or l.startswith('Meta Class'):
            cur, sect = None, None
            meta = l.startswith('Meta Class')
            continue
        if re.match(r'^ {8}name ', l) and cur is None and not meta:
            cur = res.setdefault(s.split()[-1], {'private': None, 'selectors': set()})
            continue
        if cur is None:
            continue
        if re.match(r'^ {8}baseMethods', l):
            sect = 'm'
        elif re.match(r'^ {8}ivars', l):
            sect = 'i'
        elif re.match(r'^ {8}\S', l):
            sect = None
        elif sect == 'm' and s.startswith(('name ', 'imp ')):
            m = re.search(r'[-+]\[\S+ ([^\]]+)\]', s)
            sel = m.group(1) if m else (s.split()[-1] if s.startswith('name ') and not s.endswith(')') else None)
            if sel in SELECTORS:
                cur['selectors'].add(sel)
        elif sect == 'i' and s.startswith('name '):
            pend = {'name': s.split()[-1]}
        elif sect == 'i' and s.startswith('type ') and pend.get('name') == '_private':
            t = s.split(None, 2)[2]
            if t.startswith('{'):
                cur['private'] = t
    return {c: v for c, v in res.items() if v['private'] and v['selectors']}


def fieldmap(oenc, nenc):
    """(struct, old size, new size, [(name, oo, os, ob, obits, no, ns, nb, nbits)], any field moved) for two encodings"""
    sname, osize, ol = struct_layout(oenc)
    _, nsize, nl = struct_layout(nenc)
    nmap = {p: (o, s, b, w) for p, o, s, b, w in nl}
    bitbytes = {(o, (b + w - 1) // 8 + o) for p, o, s, b, w in ol if w}
    fields, moved = [], False
    for p, o, s, b, w in ol:
        # a union member over translated bitfields (miscHash, bits) would overwrite them: the bitfields go one by one
        if s and any(lo < o + s and o <= hi for lo, hi in bitbytes):
            continue
        n = nmap.get(p)
        if n is None:
            moved = True
            fields.append((p, o, s, b, w, None, 0, 0, 0))
            continue
        if (o, s, b, w) != n:
            moved = True
        if s and n[1] != s:
            raise ValueError(f'{sname}.{p}: {s} bytes in MetalOld, {n[1]} in 27')
        fields.append((p, o, s, b, w) + n)
    return sname, osize, nsize, fields, moved


def compare(old, new):
    """[(class, selector, struct, old_size, new_size, new_encoding, [(name, oo, os, ob, obits, no, ns, nb, nbits)])]
    for every class whose MetalOld fields do not all keep their place in 27."""
    co, cn = classes(old), classes(new)
    tables = []
    for c in sorted(co):
        if c not in cn or co[c]['private'] == cn[c]['private']:
            continue
        sname, osize, nsize, fields, moved = fieldmap(co[c]['private'], cn[c]['private'])
        if not moved:
            continue
        if sname not in DRIVER_READS:
            print(f'{c} {sname}: layout differs, not read by the driver; left alone', file=sys.stderr)
            continue
        sel = sorted(co[c]['selectors'])[0]
        tables.append((c, sel, sname, osize, nsize, cn[c]['private'], fields, co[c]['private']))
    return tables


def c_string(s):
    return '"' + s.replace('\\', '\\\\').replace('"', '\\"') + '"'


def write_inc(tables, path):
    lines = ['// generated by tools/metal-adapter/abi.py from OCLP MetalOld (13.2.1-25) and 27.2.1 Metal (26B131); do not edit',
             '// LAYOUT_BEGIN(class, selector, struct, MetalOld size, 27 size, 27 encoding of _private, MetalOld encoding)',
             '// FIELD(name, MetalOld offset, size, bit, bits, 27 offset, size, bit, bits, convert): size 0 = bitfield;',
             '//   27 offset -1 = absent (left zero); convert 1 = MetalOld bit is (27 value != 0), 2 = (27 value == 1)',
             '']
    for c, sel, sname, osize, nsize, nenc, fields, oenc in tables:
        lines.append(f'LAYOUT_BEGIN({c}, {sel}, {sname}, {osize}, {nsize}, {c_string(nenc)},\n  {c_string(oenc)})')
        for p, o, s, b, w, no, ns, nb, nw in fields:
            cv = CONVERT.get(p.rsplit('.', 1)[-1], 0) if nw and nw != w else 0
            lines.append(f'  FIELD({c_string(p)}, {o}, {s}, {b}, {w}, {-1 if no is None else no}, {ns}, {nb}, {nw}, {cv})')
        lines.append(f'LAYOUT_END({c})')
        lines.append('')
    open(path, 'w').write('\n'.join(lines))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    a = sub.add_parser('layouts')
    a.add_argument('metalold')
    a.add_argument('metal27')
    a.add_argument('--out')
    s = sub.add_parser('show')
    s.add_argument('metalold')
    s.add_argument('metal27')
    s.add_argument('struct')
    args = ap.parse_args(argv)
    tables = compare(args.metalold, args.metal27)
    if args.cmd == 'layouts':
        for c, sel, sname, osize, nsize, _, fields, _ in tables:
            moved = sum(1 for f in fields if f[5] is None or f[1:5] != f[5:9])
            print(f'{c} -{sel} {sname}: MetalOld {osize} B, 27 {nsize} B, {len(fields)} fields, {moved} moved or absent')
        if args.out:
            write_inc(tables, args.out)
    else:
        for c, sel, sname, osize, nsize, _, fields, _ in tables:
            if sname != args.struct and c != args.struct:
                continue
            print(f'{c} {sname}')
            for p, o, s, b, w, no, ns, nb, nw in fields:
                mark = '' if (o, s, b, w) == (no, ns, nb, nw) else '  <--'
                print(f'  {p:55} old {o:4}+{s or str(b) + ":" + str(w):>6}  27 {no if no is not None else "-":>4}+{ns or str(nb) + ":" + str(nw):>6}{mark}')


if __name__ == '__main__':
    sys.exit(main())
