#!/usr/bin/env python3
"""make.py: build hao's Metal wrapper, the x86_64 Metal.framework/Versions/A/Metal for the legacy Metal stack.

  make.py generate SPEC OUT.m [--sdk PATH] [--metalold METALOLD]   write the stubs for SPEC
  make.py build SPEC --metalold METALOLD --out DIR [--sdk PATH] [--extra FILE.m ...]
  make.py ivars METALOLD [CLASS...]                                 MetalOld's ivar offsets, from its ObjC metadata

The wrapper re-exports MetalOld.dylib (Apple's legacy Metal, shipped by OCLP's PatcherSupportPkg; the builder
downloads it, we never redistribute it) and adds what newer clients import but MetalOld lacks.

SPEC is either a gap table (spec/gap-NNN.json, recognised by its "symbols" list) or a plain list,
{"classes": [NAME...], "functions": [NAME...], "data": [NAME...], "ivars": [MACHO_NAME...]}. From a gap table the
build takes every required symbol MetalOld does not export that x86_64 code on Intel needs: what Moraea's wrapper
provides, plus anything an importer outside the Apple-GPU driver bundles uses. Symbols only those bundles (AGXMetal*,
which never load on Intel) use and Moraea does not provide are left out and listed as "excluded".

Every stub for a name the SDK headers declare is generated from them (clang's syntax tree), so signatures are Apple's:
  classes    @implementation with the header's superclass (added too when MetalOld lacks it); properties
             synthesized; every other method declared in the header returns zero (init* returns [super init]);
             NSCopying adopters copy by retain
  functions  the header's prototype, returning zero
  data       NSString constants hold their own name; other types are zero
Names the SDK headers don't declare (private API) come from src/private.m (always built in) or an --extra source;
build refuses otherwise.

Ivar offsets (_OBJC_IVAR_$_C.v): for a class MetalOld defines, the offset is exported only if MetalOld's class has
that ivar, with MetalOld's offset (read from its ObjC metadata, checked again at load with ivar_getOffset). If the
ivar does not exist the symbol is refused, never faked: importers read and write objects at that offset. For a
class the wrapper defines, the ivar comes from that class's own declaration.

Names MetalOld already exports are skipped, so nothing is defined twice; the build checks that, and that the
wrapper plus MetalOld export every selected symbol except the refused ivars. src/runtime.m is always built in: the
per-process workarounds (Moraea's, minus the hello skip), each switchable with HAO_METAL_DISABLE.
"""
import json
import os
from pathlib import Path
import re
import struct
import subprocess
import sys

HERE = Path(__file__).resolve().parent
INSTALL_NAME = '/System/Library/Frameworks/Metal.framework/Versions/A/Metal'
COMPAT, CURRENT = '1.0.0', '1.0.0'          # as OCLP's wrapper; clients link Metal at compatibility 1.0.0
HEADER = '#import <Metal/Metal.h>\n'
SOURCES = [HERE / 'src' / 'runtime.m', HERE / 'src' / 'private.m']
CLASS_PREFIX, METACLASS_PREFIX, IVAR_PREFIX = '_OBJC_CLASS_$_', '_OBJC_METACLASS_$_', '_OBJC_IVAR_$_'


def sdk_path(sdk=None):
    return sdk or subprocess.run(['xcrun', '--sdk', 'macosx', '--show-sdk-path'], capture_output=True, text=True,
                                 check=True).stdout.strip()


def ast_objects(name, sdk, scratch):
    """Every top-level declaration clang dumps for -ast-dump-filter=NAME (a substring filter)."""
    src = Path(scratch) / 'h.m'
    src.write_text(HEADER)
    out = subprocess.run(['xcrun', 'clang', '-x', 'objective-c', '-isysroot', sdk, '-target', 'x86_64-apple-macos27.0',
                          '-fsyntax-only', '-Xclang', '-ast-dump=json', '-Xclang', '-ast-dump-filter=' + name, str(src)],
                         capture_output=True, text=True)
    raw, dec, i, objs = out.stdout, json.JSONDecoder(), 0, []
    while i < len(raw):
        while i < len(raw) and raw[i].isspace():
            i += 1
        if i >= len(raw):
            break
        obj, i = dec.raw_decode(raw, i)
        objs.append(obj)
    return objs


def exported(dylib):
    """(classes, symbols) a Mach-O exports, from nm."""
    out = subprocess.run(['nm', '-gU', str(dylib)], capture_output=True, text=True, check=True).stdout
    syms = {l.split()[-1] for l in out.splitlines() if l.strip()}
    classes = {s[len(CLASS_PREFIX):] for s in syms if s.startswith(CLASS_PREFIX)}
    return classes, syms


# --- the gap table -> a spec ------------------------------------------------------------------------------------

def spec_from_gap(gap):
    """The symbols to build from a gap table (spec/gap-NNN.json).

    Taken: required, not exported by MetalOld, and needed by x86_64 code on Intel, which is what Moraea's wrapper
    provides plus anything an importer outside the Apple-GPU driver bundles references. Left out ("excluded"):
    symbols only those bundles use that Moraea does not provide; the bundles never load on an Intel GPU."""
    spec = {'classes': [], 'functions': [], 'data': [], 'ivars': [], 'symbols': [], 'excluded': []}
    for s in gap['symbols']:
        if not s.get('required') or s['exported_by'].get('metalold'):
            continue
        if not (s['exported_by'].get('moraea_wrapper') or s['importers'].get('outside_gpu_driver_bundles', 0) > 0):
            spec['excluded'].append(s['name'])
            continue
        if s['kind'] == 'class':
            spec['classes'].append(s['name'])
        elif s.get('ivar_offset'):
            spec['ivars'].append(s['symbols'][0])
        else:
            spec['functions' if s['kind'] == 'function' else 'data'].append(s['symbols'][0][1:])
        spec['symbols'] += s['symbols']
    return spec


def load_spec(path):
    data = json.loads(Path(path).read_text())
    return spec_from_gap(data) if isinstance(data.get('symbols'), list) and 'importers' in data else data


# --- MetalOld's ObjC metadata (static) ---------------------------------------------------------------------------

class MachO:
    """Just enough of a 64-bit Mach-O (thin, or the x86_64 slice of a fat file) to read pointers, with chained
    fixups decoded (DYLD_CHAINED_PTR_64 and _64_OFFSET)."""

    def __init__(self, path):
        d = Path(path).read_bytes()
        base = 0
        if struct.unpack_from('>I', d, 0)[0] == 0xcafebabe:
            for k in range(struct.unpack_from('>I', d, 4)[0]):
                cpu, _, off, _, _ = struct.unpack_from('>iiIII', d, 8 + 20 * k)
                if cpu == 0x01000007:
                    base = off
        self.d, self.base = d, base
        if struct.unpack_from('<I', d, base)[0] != 0xfeedfacf:
            raise ValueError('%s: not a 64-bit Mach-O' % path)
        ncmds = struct.unpack_from('<I', d, base + 16)[0]
        off, self.segs, self.sects, self.imports, self.fmt, self.text = base + 32, [], {}, [], None, 0
        chained = symtab = None
        for _ in range(ncmds):
            cmd, size = struct.unpack_from('<II', d, off)
            if cmd == 0x19:
                seg = d[off + 8:off + 24].rstrip(b'\0').decode()
                vm, vms, fo, fs = struct.unpack_from('<QQQQ', d, off + 24)
                self.segs.append((vm, fo, fs))
                if seg == '__TEXT':
                    self.text = vm
                for k in range(struct.unpack_from('<I', d, off + 64)[0]):
                    so = off + 72 + 80 * k
                    name = d[so:so + 16].rstrip(b'\0').decode()
                    self.sects[(seg, name)] = struct.unpack_from('<QQ', d, so + 32)
            elif cmd == 0x80000034:
                chained = struct.unpack_from('<I', d, off + 8)[0]
            elif cmd == 0x2:
                symtab = struct.unpack_from('<4I', d, off + 8)
            off += size
        self.defined = {}
        if symtab:
            symoff, nsyms, stroff, _ = symtab
            for k in range(nsyms):
                strx, ntype, sect, _desc, value = struct.unpack_from('<IBBHQ', d, base + symoff + 16 * k)
                if sect and not ntype & 0xe0:
                    s = base + stroff + strx
                    self.defined[d[s:d.index(b'\0', s)].decode()] = value
        if chained is not None:
            self._chained(chained)

    def _chained(self, dataoff):
        h = self.base + dataoff
        _ver, starts, imports, symbols, count, ifmt, _sfmt = struct.unpack_from('<7I', self.d, h)
        for k in range(struct.unpack_from('<I', self.d, h + starts)[0]):
            info = struct.unpack_from('<I', self.d, h + starts + 4 + 4 * k)[0]
            if info:
                self.fmt = struct.unpack_from('<H', self.d, h + starts + info + 6)[0]
        width = {1: 4, 2: 8, 3: 16}[ifmt]
        for k in range(count):
            e = h + imports + width * k
            name_off = struct.unpack_from('<Q', self.d, e)[0] >> 32 if ifmt == 3 \
                else struct.unpack_from('<I', self.d, e)[0] >> 9
            s = h + symbols + name_off
            self.imports.append(self.d[s:self.d.index(b'\0', s)].decode())

    def read(self, addr, n):
        for vm, fo, fs in self.segs:
            if vm <= addr < vm + fs:
                o = self.base + fo + addr - vm
                return self.d[o:o + n]
        raise ValueError('address 0x%x is not in the file' % addr)

    def u32(self, addr):
        return struct.unpack('<I', self.read(addr, 4))[0]

    def ptr(self, addr):
        """The pointer stored at addr: an address, or the imported symbol's name for a bind to another image
        (a bind to a symbol this image defines, as ld emits for exported classes, resolves to its address)."""
        raw = struct.unpack('<Q', self.read(addr, 8))[0]
        if self.fmt in (2, 6):
            if raw >> 63:
                name = self.imports[raw & 0xffffff]
                return self.defined[name] + ((raw >> 24) & 0xff) if name in self.defined else name
            t = (raw & 0xfffffffff) | (((raw >> 36) & 0xff) << 56)
            return t + self.text if self.fmt == 6 else t
        return raw

    def cstr(self, addr):
        b = self.read(addr, 4096)
        return b[:b.index(b'\0')].decode()


def objc_classes(path):
    """{class: {"superclass", "instance_size", "ivars": {name: {"offset", "address"}}}} for every class a Mach-O
    defines (__objc_classlist), read statically. "address" is where the ivar's offset variable lives."""
    m = MachO(path)
    lst = m.sects.get(('__DATA_CONST', '__objc_classlist')) or m.sects.get(('__DATA', '__objc_classlist'))
    if not lst:
        return {}
    by_addr, out = {}, {}
    for i in range(0, lst[1], 8):
        cls = m.ptr(lst[0] + i)
        ro = m.ptr(cls + 32) & ~7
        name = m.cstr(m.ptr(ro + 24))
        by_addr[cls] = name
        ivars = {}
        il = m.ptr(ro + 48)
        if il:
            entsize, count = m.u32(il) & ~3, m.u32(il + 4)
            for k in range(count):
                e = il + 8 + entsize * k
                where = m.ptr(e)
                ivars[m.cstr(m.ptr(e + 8))] = {'offset': struct.unpack('<i', m.read(where, 4))[0], 'address': where}
        out[name] = {'superclass': m.ptr(cls + 8), 'instance_size': m.u32(ro + 8), 'ivars': ivars}
    for c in out.values():
        s = c['superclass']
        c['superclass'] = s[len(CLASS_PREFIX):] if isinstance(s, str) and s.startswith(CLASS_PREFIX) \
            else by_addr.get(s) if s else None
    return out


def split_ivar(symbol):
    """'_OBJC_IVAR_$__MTLCommandBuffer._queue' -> ('_MTLCommandBuffer', '_queue')."""
    cls, _, ivar = symbol[len(IVAR_PREFIX):].partition('.')
    return cls, ivar


# --- stubs from the SDK headers --------------------------------------------------------------------------------

def zero_return(ret):
    if ret == 'void':
        return ''
    bare = re.sub(r'\b(_Nullable|_Nonnull|_Null_unspecified|const|__kindof|__strong|__weak)\b', '', ret).strip()
    if bare in ('instancetype', 'id', 'Class', 'SEL') or bare.startswith('id<') or bare.endswith('*') or '(^' in bare:
        return '    return 0;\n'
    return '    %s __r; memset(&__r, 0, sizeof(__r)); return __r;\n' % ret


def method_stub(m):
    ret = m['returnType']['qualType']
    params = [p for p in m.get('inner', []) if p.get('kind') == 'ParmVarDecl']
    sel = m['name']
    if params:
        parts = sel.split(':')[:-1]
        head = ' '.join('%s:(%s)a%d' % (part, p['type']['qualType'], i) for i, (part, p) in enumerate(zip(parts, params)))
    else:
        head = sel
    sign = '-' if m.get('instance') else '+'
    if m.get('instance') and sel.startswith('init') and ret in ('instancetype', 'id'):
        body = '    return [super init];\n'
    else:
        body = zero_return(ret)
    return '%s (%s)%s {\n%s}\n' % (sign, ret, head, body)


def class_stub(name, objs):
    """(source, superclass name) for a class the headers declare, or None."""
    decls = [o for o in objs if o.get('kind') == 'ObjCInterfaceDecl' and o.get('name') == name]
    if not decls:
        return None
    methods, props, protocols, seen, superclass, resettable = [], [], set(), set(), None, {}
    for d in decls:
        protocols |= {p.get('name') for p in d.get('protocols', [])}
        superclass = superclass or (d.get('super') or {}).get('name')
        for c in d.get('inner', []):
            if c.get('kind') == 'ObjCPropertyDecl':
                props.append(c['name'])
                if c.get('null_resettable') and not c.get('class'):
                    resettable[c['name']] = c['type'].get('desugaredQualType', c['type']['qualType'])
            elif c.get('kind') == 'ObjCMethodDecl' and not c.get('isImplicit'):
                key = ('-' if c.get('instance') else '+') + c['name']
                if key not in seen:
                    seen.add(key)
                    methods.append(c)
    accessors = set()
    for p in props:
        accessors |= {'-' + p, '-set%s%s:' % (p[:1].upper(), p[1:])}
    lines = ['@implementation %s' % name]
    lines += ['@synthesize %s = _%s;' % (p, p) for p in dict.fromkeys(props)]
    # null_resettable: the getter never answers nil; a nil value (never set, or reset) becomes a default instance
    for p, t in resettable.items():
        cls = re.sub(r'\b(_Nullable|_Nonnull|_Null_unspecified|__kindof)\b', '', t).replace('*', '').strip()
        if re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', cls):
            lines.append('- (%s *)%s {\n    if (!_%s) _%s = [[%s alloc] init];\n    return _%s;\n}' % (cls, p, p, p, cls, p))
    if 'NSCopying' in protocols:
        lines.append('- (id)copyWithZone:(NSZone *)zone {\n    return [self retain];\n}')
    for m in methods:
        key = ('-' if m.get('instance') else '+') + m['name']
        if key in accessors or key == '-copyWithZone:':
            continue
        lines.append(method_stub(m).rstrip('\n'))
    lines.append('@end\n')
    return '\n'.join(lines), superclass


def function_stub(name, objs):
    for o in objs:
        if o.get('kind') == 'FunctionDecl' and o.get('name') == name and o.get('storageClass') != 'static' \
                and not o.get('inline'):
            ret = o['type']['qualType'].split('(')[0].strip()
            params = [p for p in o.get('inner', []) if p.get('kind') == 'ParmVarDecl']
            args = ', '.join('%s a%d' % (p['type']['qualType'], i) for i, p in enumerate(params)) or 'void'
            return '%s %s(%s) {\n%s}\n' % (ret, name, args, zero_return(ret))
    return None


def data_stub(name, objs):
    for o in objs:
        if o.get('kind') == 'VarDecl' and o.get('name') == name:
            t = o['type']['qualType']
            if 'NSString' in t:
                return '%s %s = @"%s";\n' % (t, name, name)
            return '%s %s;\n' % (t, name)
    return None


def ivar_exports(rows):
    """C for ivar-offset symbols of MetalOld's classes: MetalOld's static offset, checked at load."""
    if not rows:
        return ''
    out = ["// Ivar offsets of MetalOld's own classes, from its ObjC metadata; checked against the runtime at load.",
           '#import <objc/runtime.h>']
    for i, r in enumerate(rows):
        out.append('long hao_ivar_%d __asm__("%s") = %d;   // %s.%s' % (i, r['symbol'], r['offset'], r['class'], r['ivar']))
    out.append('static const struct { long *slot; const char *cls, *ivar; } hao_ivars[] = {')
    out += ['    {&hao_ivar_%d, "%s", "%s"},' % (i, r['class'], r['ivar']) for i, r in enumerate(rows)]
    out += ['};',
            '__attribute__((constructor)) static void hao_check_ivars(void) {',
            '    for (size_t i = 0; i < sizeof hao_ivars / sizeof hao_ivars[0]; i++) {',
            '        Class c = objc_getClass(hao_ivars[i].cls);',
            '        Ivar v = c ? class_getInstanceVariable(c, hao_ivars[i].ivar) : NULL;',
            '        if (!v) { NSLog(@"hao-metal: MetalOld has no %s.%s", hao_ivars[i].cls, hao_ivars[i].ivar); continue; }',
            '        if (ivar_getOffset(v) != *hao_ivars[i].slot) {',
            '            NSLog(@"hao-metal: %s.%s is at %td, not %ld", hao_ivars[i].cls, hao_ivars[i].ivar,',
            '                  ivar_getOffset(v), *hao_ivars[i].slot);',
            '            *hao_ivars[i].slot = ivar_getOffset(v);',
            '        }',
            '    }',
            '}', '']
    return '\n'.join(out)


def generate(spec, sdk=None, metalold=None, scratch=None):
    """(source text, report). report lists what was generated, skipped (already in MetalOld), undeclared (not in
    the SDK, so private.m must provide it), the ivar offsets taken from MetalOld, those left to our own classes,
    and those refused."""
    sdk = sdk_path(sdk)
    scratch = scratch or os.environ.get('TMPDIR', '/tmp')
    present_classes, present_syms = exported(metalold) if metalold else (set(), set())
    old_objc = objc_classes(metalold) if metalold and spec.get('ivars') else {}
    parts = ['// Generated by tools/metal-wrapper/make.py from the SDK\'s Metal headers. Do not edit.',
             HEADER.rstrip(), '#import <string.h>',
             '#pragma clang diagnostic ignored "-Wobjc-property-implementation"',
             '#pragma clang diagnostic ignored "-Wincomplete-implementation"',
             '#pragma clang diagnostic ignored "-Wunguarded-availability-new"',
             '#pragma clang diagnostic ignored "-Wdeprecated-implementations"',
             '#pragma clang diagnostic ignored "-Wobjc-designated-initializers"', '']
    report = {'classes': [], 'functions': [], 'data': [], 'skipped': [], 'undeclared': [], 'added_superclasses': [],
              'ivars_metalold': [], 'ivars_ours': [], 'refused': [], 'excluded': list(spec.get('excluded', []))}
    queue, wanted = list(spec.get('classes', [])), set(spec.get('classes', []))
    while queue:
        name = queue.pop(0)
        if name in present_classes:
            report['skipped'].append(name)
            continue
        made = class_stub(name, ast_objects(name, sdk, scratch))
        if made is None:
            report['undeclared'].append(name)
            continue
        text, superclass = made
        parts.append(text)
        report['classes'].append(name)
        if superclass and superclass.startswith('MTL') and superclass not in present_classes \
                and superclass not in wanted:
            wanted.add(superclass)
            queue.append(superclass)
            report['added_superclasses'].append(superclass)
    for kind, make in (('functions', function_stub), ('data', data_stub)):
        for name in spec.get(kind, []):
            if '_' + name in present_syms:
                report['skipped'].append(name)
                continue
            text = make(name, ast_objects(name, sdk, scratch))
            if text is None:
                report['undeclared'].append(name)
                continue
            parts.append(text)
            report[kind].append(name)
    rows = []
    for symbol in spec.get('ivars', []):
        cls, ivar = split_ivar(symbol)
        if symbol in present_syms:
            report['skipped'].append(symbol)
        elif cls in old_objc:
            have = old_objc[cls]['ivars'].get(ivar)
            if have:
                rows.append({'symbol': symbol, 'class': cls, 'ivar': ivar, 'offset': have['offset']})
                report['ivars_metalold'].append({'symbol': symbol, 'offset': have['offset']})
            else:
                report['refused'].append({'symbol': symbol, 'reason': "MetalOld's %s has no ivar %s; an offset would "
                                          "make importers read and write unrelated memory" % (cls, ivar)})
        else:
            report['ivars_ours'].append(symbol)
    parts.append(ivar_exports(rows))
    return '\n'.join(parts) + '\n', report


# --- build ------------------------------------------------------------------------------------------------------

def check_exports(spec, report, wrapper, metalold):
    """(missing, doubled): selected symbols neither the wrapper nor MetalOld exports (refused ivars aside), and
    symbols both define."""
    _, ours = exported(wrapper)
    _, old = exported(metalold)
    refused = {r['symbol'] for r in report['refused']}
    wanted = set(spec.get('symbols', []))
    wanted |= {CLASS_PREFIX + c for c in spec.get('classes', [])} | {'_' + n for n in spec.get('functions', [])}
    wanted |= {'_' + n for n in spec.get('data', [])} | set(spec.get('ivars', []))
    missing = sorted(s for s in wanted - refused if s not in ours and s not in old)
    doubled = sorted(ours & old)
    return missing, doubled


def build(spec, metalold, out, sdk=None, extra=(), defines=()):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    source, report = generate(spec, sdk, metalold, scratch=str(out))
    sources = [*SOURCES, *map(Path, extra)]
    private_text = ''.join(p.read_text() for p in sources[1:])
    missing = [n for n in report['undeclared']           # by name, or by Mach-O name in an __asm__ label
               if not re.search(r'(?<![A-Za-z0-9_])_?%s(?![A-Za-z0-9_])' % re.escape(n), private_text)]
    if missing:
        raise SystemExit('make.py: not in the SDK headers, src/private.m or --extra: %s' % ', '.join(missing))
    (out / 'stubs.m').write_text(source)
    cmd = ['xcrun', 'clang', '-arch', 'x86_64', '-isysroot', sdk_path(sdk), '-mmacosx-version-min=27.0',
           '-dynamiclib', '-fno-objc-arc', '-O1', '-Wall', '-Wno-unused-parameter', *['-D' + d for d in defines],
           '-install_name', INSTALL_NAME, '-compatibility_version', COMPAT, '-current_version', CURRENT,
           '-framework', 'Foundation', '-lobjc', '-Wl,-reexport_library,' + str(metalold),
           str(out / 'stubs.m'), *map(str, sources), '-o', str(out / 'Metal')]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode:
        raise SystemExit('make.py: clang failed:\n' + r.stderr[-4000:])
    subprocess.run(['codesign', '-f', '-s', '-', '-i', 'Metal', str(out / 'Metal')], check=True, capture_output=True)
    report['missing'], report['doubled'] = check_exports(spec, report, out / 'Metal', metalold)
    (out / 'report.json').write_text(json.dumps(report, indent=1) + '\n')
    if report['missing'] or report['doubled']:
        raise SystemExit('make.py: not exported by the wrapper or MetalOld: %s; defined in both: %s' % (
            ', '.join(report['missing']) or 'none', ', '.join(report['doubled']) or 'none'))
    return report


def main(argv):
    import argparse
    p = argparse.ArgumentParser(prog='make.py', description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest='action', required=True)
    g = sub.add_parser('generate')
    g.add_argument('spec'); g.add_argument('out'); g.add_argument('--sdk'); g.add_argument('--metalold')
    b = sub.add_parser('build')
    b.add_argument('spec'); b.add_argument('--metalold', required=True); b.add_argument('--out', required=True)
    b.add_argument('--sdk'); b.add_argument('--extra', action='append', default=[])
    v = sub.add_parser('ivars')
    v.add_argument('metalold'); v.add_argument('classes', nargs='*')
    a = p.parse_args(argv)
    if a.action == 'ivars':
        classes = objc_classes(a.metalold)
        for name in a.classes or sorted(classes):
            c = classes.get(name)
            if c is None:
                print('%s: not defined' % name)
                continue
            print('%s : %s (%d bytes)' % (name, c['superclass'], c['instance_size']))
            for iv, info in c['ivars'].items():
                print('    %-40s %5d' % (iv, info['offset']))
        return 0
    spec = load_spec(a.spec)
    if a.action == 'generate':
        source, report = generate(spec, a.sdk, a.metalold)
        Path(a.out).write_text(source)
        print(json.dumps({k: len(v) if k != 'undeclared' else v for k, v in report.items()}))
        return 0
    report = build(spec, a.metalold, a.out, a.sdk, a.extra)
    print('make.py: %d classes (%d added as superclasses), %d functions, %d data generated from the SDK; '
          '%d private names from src/private.m; %d ivar offsets from MetalOld, %d from our classes, %d refused; '
          '%d already in MetalOld; %d excluded (Apple-GPU driver bundles only)' % (
              len(report['classes']), len(report['added_superclasses']), len(report['functions']),
              len(report['data']), len(report['undeclared']), len(report['ivars_metalold']),
              len(report['ivars_ours']), len(report['refused']), len(report['skipped']), len(report['excluded'])))
    for r in report['refused']:
        print('make.py: refused %s: %s' % (r['symbol'], r['reason']))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
