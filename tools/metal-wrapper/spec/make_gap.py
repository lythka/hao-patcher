#!/usr/bin/env python3.14
"""Build the Metal wrapper gap table: every Metal class, function and data symbol x86_64 code of one macOS build
references, and whether that build's Metal, MetalOld and Moraea's wrapper export it.

    python3.14 -I tools/metal-wrapper/spec/make_gap.py --build 26B131 \\
        --cache X86_64_DYLD_CACHE --fs FS_ROOT --fs rosetta=ROSETTA_CRYPTEX_ROOT \\
        --metalold METALOLD_DYLIB --wrapper MORAEA_METAL --oclp-artifact OCLP_ARTIFACT_DIR \\
        --out tools/metal-wrapper/spec/gap-26B131.json

Needs `ipsw` (blacktop/ipsw; `dyld imports`, `dyld macho -n/-o/-r/-l`, `dyld patches`) for the shared cache and
`dyld_info` for on-disk Mach-O files. Read-only on every input. See README.md for the method and its limits.
"""
import argparse
import concurrent.futures
import json
import os
import re
import struct
import subprocess
import sys
from pathlib import Path

METAL_PATH = '/System/Library/Frameworks/Metal.framework/Versions/A/Metal'
METAL_LEAF = 'Metal'
CLASS_PREFIX = '_OBJC_CLASS_$_'
METACLASS_PREFIX = '_OBJC_METACLASS_$_'
IVAR_PREFIX = '_OBJC_IVAR_$_'

CPU_TYPE_X86_64 = 0x01000007
CPU_TYPE_ARM64 = 0x0100000C
MH_MAGIC_64 = 0xFEEDFACF
FAT_MAGIC = 0xCAFEBABE
FAT_MAGIC_64 = 0xCAFEBABF
DYLIB_LOAD_COMMANDS = {
    0x0C: 'load',              # LC_LOAD_DYLIB
    0x80000018: 'weak',        # LC_LOAD_WEAK_DYLIB
    0x8000001F: 'reexport',    # LC_REEXPORT_DYLIB
    0x20: 'lazy',              # LC_LAZY_LOAD_DYLIB
    0x80000023: 'upward',      # LC_LOAD_UPWARD_DYLIB
}


# ---------------------------------------------------------------------------------------------------------------
# Parsers (pure functions on tool output or bytes; tests feed them fixtures)

def parse_ipsw_imports(text):
    """`ipsw dyld imports CACHE DYLIB` -> (cache dylib paths, file-system paths).

    The file-system section lists programs the cache has prebuilt launch info for; `/cdhash/...` lines repeat
    a path in parentheses and are skipped, and `PATH (DYLIB)` lines keep PATH's dylib in parentheses."""
    cache, fs, section = [], [], None
    for line in text.splitlines():
        if line.startswith('In DSC'):
            section = cache
            continue
        if line.startswith('In FileSystem'):
            section = fs
            continue
        if section is None or not line.startswith('/'):
            continue
        if line.startswith('/cdhash/'):
            continue
        m = re.match(r'^(/.*?)(?: \((/[^)]*)\))?$', line)
        path = m.group(2) or m.group(1) if section is fs else m.group(1)
        if path not in section:
            section.append(path)
    return cache, fs


def normalize(name):
    """ipsw prints ObjC ivar offset symbols without their leading underscore (`OBJC_IVAR_$_C.ivar` for the Mach-O
    name `_OBJC_IVAR_$_C.ivar`; checked against Metal's export trie). Restore it so names match dyld_info's."""
    return '_' + name if name.startswith('OBJC_') else name


_SYMTAB_RE = re.compile(r'^(?:0x[0-9a-fA-F]+:)?\s*(\((?:undefined|[^)]*)\))\s+([^\t]*)\t([^\t]+)\t?\s*(?:\(from ([^)]+)\))?')


def parse_ipsw_symtab(text):
    """`ipsw dyld macho CACHE DYLIB -n` -> {'defined': {name: section}, 'exported': {name: section},
    'undefined': [(name, from, weak)]}. `section` is 'SEG,SECT'."""
    defined, exported, undefined = {}, {}, []
    for line in text.splitlines():
        m = _SYMTAB_RE.match(line)
        if not m:
            continue
        where, attrs, name, lib = m.group(1)[1:-1], m.group(2).strip(), normalize(m.group(3).strip()), m.group(4)
        if where == 'undefined':
            undefined.append((name, lib, attrs.startswith('weak')))
            continue
        defined[name] = where
        if 'external' in attrs.split() and not attrs.startswith('non-external'):
            exported[name] = where
    return {'defined': defined, 'exported': exported, 'undefined': undefined}


_INTERFACE_RE = re.compile(r'^@interface\s+([A-Za-z_$][\w$.]*)\s*(?:\(([^)]*)\)|:\s*([A-Za-z_$][\w$.]*))?')
_REF_RE = re.compile(r'^0x[0-9a-fA-F]+\s*=>\s*0x[0-9a-fA-F]+:\s*(\S+)')


def parse_ipsw_objc(text):
    """`ipsw dyld macho CACHE DYLIB -o -r` -> {'classes': {name: superclass or None}, 'categories': [(class, cat)],
    'classrefs': [names]}. Super refs name the image's own classes (targets of [super ...]), so they are not
    references to other images and are not returned."""
    classes, categories, classrefs, section = {}, [], [], None
    for line in text.splitlines():
        if line.startswith('@interface'):
            m = _INTERFACE_RE.match(line)
            if m:
                if m.group(2) is not None:
                    categories.append((m.group(1), m.group(2)))
                else:
                    classes[m.group(1)] = m.group(3)
            continue
        if re.match(r'^@\w+ refs\s*$', line):
            section = line.split()[0][1:]
            continue
        if section == 'class':
            m = _REF_RE.match(line)
            if m:
                classrefs.append(m.group(1))
    return {'classes': classes, 'categories': categories, 'classrefs': classrefs}


_DI_IMPORT_RE = re.compile(r'^\s+0x[0-9A-Fa-f]+\s+(\S+)\s*(\[weak-import\])?\s*\(from ([^)]+)\)')


def parse_dyld_info_imports(text):
    """`dyld_info -arch x86_64 -imports FILE` -> [(name, from, weak)]."""
    out = []
    for line in text.splitlines():
        m = _DI_IMPORT_RE.match(line)
        if m:
            out.append((m.group(1), m.group(3), bool(m.group(2))))
    return out


def parse_dyld_info_exports(text):
    """`dyld_info -arch x86_64 -exports FILE` -> set of exported names (re-exports included as listed)."""
    out = set()
    for line in text.splitlines():
        m = re.match(r'^\s+0x[0-9A-Fa-f]+\s+(\S+)', line)
        if m:
            out.add(m.group(1))
    return out


def parse_dyld_info_linked(text):
    """`dyld_info -arch x86_64 -linked_dylibs FILE` -> [(attributes, path)]."""
    out = []
    for line in text.splitlines():
        m = re.match(r'^\s{8}(\S.*?)?\s+(/\S.*)$', line)
        if m and not line.strip().startswith('attributes'):
            out.append(((m.group(1) or '').strip(), m.group(2).strip()))
    return out


def macho_x86_64_dylibs(read, cpu=CPU_TYPE_X86_64):
    """Dylib load commands of the x86_64 (or `cpu`) slice of a Mach-O file, or None when it has none.

    `read(offset, size)` returns bytes from the file. Returns [(kind, path)], kind from DYLIB_LOAD_COMMANDS."""
    head = read(0, 8)
    if len(head) < 8:
        return None
    magic_be = struct.unpack('>I', head[:4])[0]
    slice_offset = None
    if magic_be in (FAT_MAGIC, FAT_MAGIC_64):
        nfat = struct.unpack('>I', head[4:8])[0]
        if nfat > 64:          # Java class files share 0xCAFEBABE; their minor/major version is large
            return None
        size = 32 if magic_be == FAT_MAGIC_64 else 20
        table = read(8, nfat * size)
        for i in range(nfat):
            entry = table[i * size:(i + 1) * size]
            if len(entry) < size:
                return None
            cputype = struct.unpack('>i', entry[:4])[0] & 0xFFFFFFFF
            if cputype == cpu:
                slice_offset = (struct.unpack('>Q', entry[8:16])[0] if magic_be == FAT_MAGIC_64
                                else struct.unpack('>I', entry[8:12])[0])
                break
        if slice_offset is None:
            return None
    elif struct.unpack('<I', head[:4])[0] == MH_MAGIC_64 and struct.unpack('<I', head[4:8])[0] == cpu:
        slice_offset = 0
    else:
        return None
    hdr = read(slice_offset, 32)
    if len(hdr) < 32:
        return None
    magic, cputype, _sub, _ftype, ncmds, sizeofcmds, _flags, _res = struct.unpack('<IiiIIIII', hdr)
    if magic != MH_MAGIC_64 or (cputype & 0xFFFFFFFF) != cpu or sizeofcmds > 16 << 20:
        return None
    cmds = read(slice_offset + 32, sizeofcmds)
    out, pos = [], 0
    for _ in range(ncmds):
        if pos + 8 > len(cmds):
            break
        cmd, cmdsize = struct.unpack('<II', cmds[pos:pos + 8])
        if cmdsize < 8:
            break
        if cmd in DYLIB_LOAD_COMMANDS and cmdsize >= 24:
            name_off = struct.unpack('<I', cmds[pos + 8:pos + 12])[0]
            raw = cmds[pos + name_off:pos + cmdsize]
            out.append((DYLIB_LOAD_COMMANDS[cmd], raw.split(b'\0', 1)[0].decode('utf-8', 'replace')))
        pos += cmdsize
    return out


def parse_export_trie(data):
    """Names in a Mach-O export trie (LC_DYLD_EXPORTS_TRIE or dyld info export bytes)."""
    def uleb(i):
        value = shift = 0
        while True:
            b = data[i]
            i += 1
            value |= (b & 0x7F) << shift
            shift += 7
            if b < 0x80:
                return value, i

    names, stack, seen = [], [(0, '')], set()
    while stack:
        off, prefix = stack.pop()
        if off in seen or off >= len(data):
            raise ValueError(f'bad export trie node at {off:#x}')
        seen.add(off)
        size, i = uleb(off)
        if size:
            names.append(prefix)
        i += size
        count = data[i]
        i += 1
        children = []
        for _ in range(count):
            end = data.index(b'\0', i)
            edge = data[i:end].decode('utf-8', 'replace')
            child, i = uleb(end + 1)
            children.append((child, prefix + edge))
        stack.extend(reversed(children))
    return sorted(names)


def parse_ipsw_patches(text, owner=METAL_LEAF):
    """`ipsw dyld patches CACHE -i DYLIB` -> {exported symbol: set of client image leaf names}, the dyld cache patch
    table dyld uses to rebind cache clients to an on-disk replacement of DYLIB. Uses through the cache's shared GOT
    are not attributed to an image and appear as 'GOT'; the dylib's own uses are dropped."""
    out, cur = {}, None
    for line in text.splitlines():
        m = re.match(r'^0x[0-9a-fA-F]+:\s+(\S+)\s*$', line)
        if m:
            cur = out.setdefault(normalize(m.group(1)), set())
            continue
        m = re.match(r'^\s+0x[0-9a-fA-F]+:\s+(\S+)(?:\s+(\S+))?', line)
        if m and cur is not None:
            kind, image = m.group(1), m.group(2) or ''
            if kind == 'PATCH' and image == owner:
                continue
            cur.add(image if kind == 'PATCH' else kind)
    return out


def parse_ipsw_exports_trie_command(text):
    """(file offset, size) of LC_DYLD_EXPORTS_TRIE in `ipsw dyld macho CACHE DYLIB -l` output, or None."""
    m = re.search(r'LC_DYLD_EXPORTS_TRIE\s+offset=(0x[0-9a-fA-F]+)\s+size=(0x[0-9a-fA-F]+)', text)
    return (int(m.group(1), 16), int(m.group(2), 16)) if m else None


GPU_DRIVER_BUNDLE_RE = re.compile(r'^(?:\[[^\]]*\])?/System/Library/Extensions/[^/]+\.bundle/')


def is_gpu_driver_bundle(importer):
    """A Metal driver bundle (AGXMetal*, AppleParavirtGPU*, ...). Metal loads the one matching the GPU; on Intel
    under OCLP that is OCLP's own HD 4000 bundle, so no 26B131 driver bundle loads."""
    return bool(GPU_DRIVER_BUNDLE_RE.match(importer))


def symbol_kind(name, metal_section=None):
    """(kind, entity) for an imported Metal symbol. kind is class, function or data; entity is the class name for
    class symbols and the symbol name otherwise. `metal_section` is where the build's Metal defines it."""
    for prefix in (CLASS_PREFIX, METACLASS_PREFIX):
        if name.startswith(prefix):
            return 'class', name[len(prefix):]
    if name.startswith(IVAR_PREFIX):
        return 'data', name
    if metal_section is None:
        return 'unknown', name
    return ('function' if metal_section == '__TEXT,__text' else 'data'), name


def overlay_replaced_paths(install_map, payload_root, exists=os.path.exists, walk=os.walk):
    """System paths an OCLP hardware artifact installs (overwrites or merges), from its install-map.json."""
    out = set()
    for entry in install_map.get('entries', []):
        if 'Remove' in entry.get('operation', ''):
            out.add(entry['destination'])
            continue
        src = os.path.join(payload_root, entry['support_package_relative_path'])
        dest = entry['destination']
        if os.path.isdir(src):
            for dirpath, _dirs, files in walk(src):
                rel = os.path.relpath(dirpath, src)
                for f in files:
                    out.add(os.path.normpath(os.path.join(dest, rel, f)))
        elif exists(src):
            out.add(dest)
    return out


# ---------------------------------------------------------------------------------------------------------------
# Assembly (pure: takes parsed inputs, returns the table)

def build_table(refs, metal_exports, metalold_exports, wrapper_exports, wrapper_reexports_metalold,
                replaced=frozenset(), max_names=5):
    """refs: [(importer, origin, symbol, weak, source)]; *_exports: {name: section or None}.

    Returns (symbols, summary). A symbol is counted as required when at least one importer that is not replaced
    by the OCLP overlay references it."""
    by_entity = {}
    for importer, origin, sym, weak, source in refs:
        kind, entity = symbol_kind(sym, metal_exports.get(sym))
        key = (kind, entity)
        rec = by_entity.setdefault(key, {'symbols': set(), 'importers': {}, 'sources': set()})
        rec['symbols'].add(sym)
        rec['sources'].add(source)
        imp = rec['importers'].setdefault(importer, {'origin': origin, 'weak': True})
        imp['weak'] = imp['weak'] and weak

    def exported(exports, kind, entity, syms):
        if kind == 'class':
            return CLASS_PREFIX + entity in exports
        return any(s in exports for s in syms)

    symbols = []
    for (kind, entity), rec in sorted(by_entity.items(), key=lambda kv: (kv[0][0], kv[0][1])):
        syms = rec['symbols']
        in_metal = exported(metal_exports, kind, entity, syms)
        in_old = exported(metalold_exports, kind, entity, syms)
        in_wrapper = exported(wrapper_exports, kind, entity, syms)
        live = {n: i for n, i in rec['importers'].items() if n not in replaced}
        order = sorted(live.items(), key=lambda kv: (is_gpu_driver_bundle(kv[0]), kv[1]['weak'],
                                                      kv[1]['origin'] != 'cache', kv[0]))
        provided = in_wrapper or (wrapper_reexports_metalold and in_old)
        symbols.append({
            'name': entity,
            'kind': kind,
            'symbols': sorted(syms),
            'importers': {
                'count': len(live),
                'strong': sum(1 for i in live.values() if not i['weak']),
                'cache': sum(1 for i in live.values() if i['origin'] == 'cache'),
                'disk': sum(1 for i in live.values() if i['origin'] != 'cache'),
                'outside_gpu_driver_bundles': sum(1 for n in live if not is_gpu_driver_bundle(n)),
                'names': [n for n, _ in order[:max_names]],
                'replaced_by_oclp': sorted(n for n in rec['importers'] if n in replaced),
            },
            'subclassed': any(s.startswith(METACLASS_PREFIX) for s in syms),
            'ivar_offset': kind == 'data' and entity.startswith(IVAR_PREFIX),
            'exported_by': {'metal': in_metal, 'metalold': in_old, 'moraea_wrapper': in_wrapper},
            'provided_today': provided,
            'required': bool(live),
            'sources': sorted(rec['sources']),
        })
    summary = summarize(symbols)
    return symbols, summary


def arm64_extra(arm64_only, symbols, metal_exports, metalold_exports, wrapper_exports):
    """Metal symbols that only arm64-only programs (run by hao-runtime, not counted above) reference: not part of
    the x86_64 table. Reported in case the runtime binds such programs to the x86_64 Metal."""
    known = {sym for s in symbols for sym in s['symbols']}
    extra = {}
    for importer, syms in arm64_only:
        for sym in syms or ():
            if sym not in known:
                extra.setdefault(sym, []).append(importer)
    out = []
    for sym, importers in sorted(extra.items()):
        kind, entity = symbol_kind(sym, metal_exports.get(sym))
        cls = CLASS_PREFIX + entity if kind == 'class' else sym
        out.append({'name': entity, 'kind': kind, 'importers': sorted(importers)[:5], 'count': len(importers),
                    'exported_by': {'metal': cls in metal_exports, 'metalold': cls in metalold_exports,
                                    'moraea_wrapper': cls in wrapper_exports}})
    return out


def summarize(symbols):
    """Per kind (and for the ivar-offset subset of data): required symbols, how many MetalOld lacks, how many of
    those Moraea's wrapper adds, and the names nobody provides, also restricted to symbols referenced from outside
    GPU driver bundles."""
    groups = {
        'class': lambda s: s['kind'] == 'class',
        'function': lambda s: s['kind'] == 'function',
        'data': lambda s: s['kind'] == 'data',
        'data_ivar_offsets': lambda s: s['kind'] == 'data' and s['ivar_offset'],
        'unknown': lambda s: s['kind'] == 'unknown',
    }
    out = {}
    for group, pick in groups.items():
        req = [s for s in symbols if pick(s) and s['required']]
        missing = [s for s in req if not s['exported_by']['metalold']]
        nobody = [s for s in missing if not s['exported_by']['moraea_wrapper']]
        out[group] = {
            'required': len(req),
            'required_strong': sum(1 for s in req if s['importers']['strong']),
            'missing_from_metalold': len(missing),
            'moraea_provides': sum(1 for s in missing if s['exported_by']['moraea_wrapper']),
            'nobody_provides': sorted(s['name'] for s in nobody),
            'nobody_provides_outside_gpu_driver_bundles': sorted(
                s['name'] for s in nobody if s['importers']['outside_gpu_driver_bundles']),
            'not_in_metal': sorted(s['name'] for s in req if not s['exported_by']['metal']),
        }
    return out


# ---------------------------------------------------------------------------------------------------------------
# Collection (runs the tools)

def run(cmd):
    p = subprocess.run(cmd, capture_output=True, text=True, errors='replace')
    return p.returncode, p.stdout + p.stderr


def run_cached(raw_dir, key, cmd):
    if raw_dir:
        f = Path(raw_dir) / (re.sub(r'[^A-Za-z0-9._-]', '_', key) + '.txt')
        if f.exists():
            return 0, f.read_text(errors='replace')
        rc, out = run(cmd)
        if rc == 0:
            f.write_text(out)
        return rc, out
    return run(cmd)


def tool_version(cmd):
    rc, out = run(cmd)
    return out.strip().splitlines()[0] if out.strip() else None


def scan_fs(root, skip_dirs):
    """Yield (relative path, 'x86_64' or 'arm64') for Mach-O regular files under root that load Metal: the x86_64
    slice when there is one, else the arm64 slice (those run only under hao-runtime and are reported, not
    counted)."""
    root = os.path.abspath(root)
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if os.path.join(dirpath, d) not in skip_dirs
                         and not os.path.islink(os.path.join(dirpath, d)))
        for name in sorted(files):
            path = os.path.join(dirpath, name)
            try:
                if os.path.islink(path) or not os.path.isfile(path) or os.path.getsize(path) < 4096:
                    continue
                with open(path, 'rb') as fh:
                    def read(off, size, fh=fh):
                        fh.seek(off)
                        return fh.read(size)
                    loads, arch = macho_x86_64_dylibs(read), 'x86_64'
                    if loads is None:
                        loads, arch = macho_x86_64_dylibs(read, CPU_TYPE_ARM64), 'arm64'
            except (OSError, struct.error):
                continue
            if loads and any(p == METAL_PATH for _k, p in loads):
                yield '/' + os.path.relpath(path, root), arch


def collect(args, log):
    ipsw = args.ipsw
    cache = args.cache
    rc, text = run([ipsw, 'dyld', 'imports', cache, METAL_PATH, '--no-color'])
    if rc:
        sys.exit(f'ipsw dyld imports failed:\n{text}')
    cache_importers, cache_fs_list = parse_ipsw_imports(text)
    log(f'cache importers of Metal: {len(cache_importers)}')

    rc, text = run_cached(args.raw_dir, 'cache-Metal-n', [ipsw, 'dyld', 'macho', cache, METAL_PATH, '-n', '--no-color'])
    metal = parse_ipsw_symtab(text)
    metal_exports = metal['exported']
    if not metal_exports:
        sys.exit('no exports parsed from the cache Metal')
    trie_check = None
    rc, text = run([ipsw, 'dyld', 'macho', cache, METAL_PATH, '-l', '--no-color'])
    where = parse_ipsw_exports_trie_command(text) if rc == 0 else None
    if where and where[0] + where[1] <= os.path.getsize(cache):
        with open(cache, 'rb') as fh:
            fh.seek(where[0])
            try:
                trie = set(parse_export_trie(fh.read(where[1])))
            except (ValueError, IndexError):
                trie = None
        if trie is not None:
            trie_check = trie == set(metal_exports)
            if not trie_check:
                log(f'warning: Metal symtab exports differ from its export trie '
                    f'({len(set(metal_exports) - trie)} only in symtab, {len(trie - set(metal_exports))} only in trie)')

    refs, errors = [], []

    def cache_one(path):
        rc_n, n_text = run_cached(args.raw_dir, 'cache-n' + path, [ipsw, 'dyld', 'macho', cache, path, '-n', '--no-color'])
        rc_o, o_text = run_cached(args.raw_dir, 'cache-or' + path,
                                  [ipsw, 'dyld', 'macho', cache, path, '-o', '-r', '--no-color'])
        return path, rc_n, n_text, rc_o, o_text

    metal_classes = {n[len(CLASS_PREFIX):] for n in metal_exports if n.startswith(CLASS_PREFIX)}
    with concurrent.futures.ThreadPoolExecutor(args.jobs) as pool:
        for path, rc_n, n_text, rc_o, o_text in pool.map(cache_one, cache_importers):
            if rc_n:
                errors.append({'importer': path, 'step': 'ipsw -n', 'output': n_text.strip()[-300:]})
                continue
            sym = parse_ipsw_symtab(n_text)
            own = {n[len(CLASS_PREFIX):] for n in sym['defined'] if n.startswith(CLASS_PREFIX)}
            for name, lib, weak in sym['undefined']:
                if lib == METAL_LEAF:
                    refs.append((path, 'cache', name, weak, 'symtab'))
            if rc_o:
                errors.append({'importer': path, 'step': 'ipsw -o -r', 'output': o_text.strip()[-300:]})
                continue
            objc = parse_ipsw_objc(o_text)
            # A superclass, category base or class ref naming a class the build's Metal exports, and the image
            # itself does not define, is a reference to Metal. Weakness is unknown from ObjC metadata alone;
            # when the symtab also lists the symbol, its flag wins (build_table ANDs weak flags).
            names = [s for s in objc['classes'].values() if s] + [c for c, _ in objc['categories']] + objc['classrefs']
            for cls in sorted(set(names)):
                if cls in metal_classes and cls not in own:
                    refs.append((path, 'cache', CLASS_PREFIX + cls, True, 'objc'))

    # Cross-check against the cache patch table: the uses dyld rebinds when Metal is replaced on disk.
    rc, text = run([ipsw, 'dyld', 'patches', cache, '-i', METAL_PATH, '--no-color'])
    patch_check = None
    if rc == 0:
        patched = {sym for sym, users in parse_ipsw_patches(text).items() if users}
        cache_syms = {sym for _imp, origin, sym, _w, _src in refs if origin == 'cache'}
        patch_check = {'patch_table_symbols_used_outside_metal': len(patched),
                       'only_in_patch_table': sorted(patched - cache_syms),
                       'only_in_importer_symtabs': sorted(cache_syms - patched)}
        log(f"patch table: {len(patched)} symbols used outside Metal; {len(patched - cache_syms)} not found in "
            f"importer symtabs, {len(cache_syms - patched)} only in importer symtabs")
    else:
        errors.append({'importer': '(cache)', 'step': 'ipsw dyld patches', 'output': text.strip()[-300:]})

    disk_importers, arm64_only = [], []
    for spec in args.fs:
        label, _, root = spec.rpartition('=')
        skip = {os.path.abspath(os.path.join(root, 'System/Library/dyld'))}
        for rel, arch in scan_fs(root, skip):
            name = f'[{label}]{rel}' if label else rel
            if arch != 'x86_64':
                rc, text = run_cached(args.raw_dir, f'disk-arm64-{label}{rel}',
                                      [args.dyld_info, '-arch', 'arm64e', '-imports', os.path.join(root, rel.lstrip('/'))])
                syms = sorted({sym for sym, lib, _w in parse_dyld_info_imports(text) if lib == METAL_LEAF}) if rc == 0 else None
                arm64_only.append((name, syms))
                continue
            disk_importers.append(name)
            rc, text = run_cached(args.raw_dir, f'disk-{label}{rel}',
                                  [args.dyld_info, '-arch', 'x86_64', '-imports', os.path.join(root, rel.lstrip('/'))])
            if rc:
                errors.append({'importer': name, 'step': 'dyld_info -imports', 'output': text.strip()[-300:]})
                continue
            for sym, lib, weak in parse_dyld_info_imports(text):
                if lib == METAL_LEAF:
                    refs.append((name, 'disk', sym, weak, 'binds'))
    log(f'on-disk x86_64 importers of Metal: {len(disk_importers)}')

    def exports_of(path):
        rc, text = run([args.dyld_info, '-arch', 'x86_64', '-exports', path])
        if rc:
            sys.exit(f'dyld_info -exports {path} failed:\n{text}')
        return parse_dyld_info_exports(text)

    metalold_exports = exports_of(args.metalold)
    wrapper_exports = exports_of(args.wrapper)
    rc, text = run([args.dyld_info, '-arch', 'x86_64', '-linked_dylibs', args.wrapper])
    linked = parse_dyld_info_linked(text)
    reexports_metalold = any('re-export' in a and p.endswith('/MetalOld.dylib') for a, p in linked)

    replaced = set()
    if args.oclp_artifact:
        imap = json.loads((Path(args.oclp_artifact) / 'install-map.json').read_text())
        replaced = overlay_replaced_paths(imap, str(Path(args.oclp_artifact) / 'payload'))

    fs_listed = {p for p in cache_fs_list}
    disk_plain = {n for n in disk_importers if not n.startswith('[')}
    unseen = sorted(fs_listed - disk_plain)

    return {
        'refs': refs, 'errors': errors, 'metal_exports': metal_exports,
        'metalold_exports': {n: None for n in metalold_exports},
        'wrapper_exports': {n: None for n in wrapper_exports},
        'wrapper_reexports_metalold': reexports_metalold,
        'replaced': replaced, 'cache_importers': cache_importers, 'disk_importers': disk_importers,
        'cache_fs_listed_not_scanned': unseen, 'arm64_only': arm64_only, 'trie_check': trie_check,
        'patch_check': patch_check,
    }


def sha256(path):
    import hashlib
    h = hashlib.sha256()
    with open(path, 'rb') as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def cache_uuid(path):
    with open(path, 'rb') as fh:
        head = fh.read(0x68)
    if not head.startswith(b'dyld_v1'):
        return None
    u = head[0x58:0x68].hex().upper()
    return f'{u[:8]}-{u[8:12]}-{u[12:16]}-{u[16:20]}-{u[20:]}'


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('--build', required=True, help='OS build the inputs belong to, e.g. 26B131')
    ap.add_argument('--cache', required=True, help='x86_64 dyld shared cache (main file; subcaches beside it)')
    ap.add_argument('--fs', action='append', default=[], metavar='[LABEL=]ROOT',
                    help='root of on-disk files to scan (repeatable); LABEL prefixes importer names')
    ap.add_argument('--metalold', required=True, help="OCLP's MetalOld.dylib")
    ap.add_argument('--wrapper', required=True, help="Moraea's Metal wrapper (OCLP's Metal.framework/Versions/A/Metal)")
    ap.add_argument('--oclp-artifact', help='OCLP hardware artifact (install-map.json + payload/): importers it '
                                            'replaces are listed but do not make a symbol required')
    ap.add_argument('--out', required=True)
    ap.add_argument('--raw-dir', help='cache raw tool output here (re-runs reuse it)')
    ap.add_argument('--ipsw', default='ipsw')
    ap.add_argument('--dyld-info', default='dyld_info')
    ap.add_argument('--jobs', type=int, default=8)
    args = ap.parse_args(argv)
    if args.raw_dir:
        os.makedirs(args.raw_dir, exist_ok=True)
    log = lambda m: print(m, file=sys.stderr)

    c = collect(args, log)
    symbols, summary = build_table(c['refs'], c['metal_exports'], c['metalold_exports'], c['wrapper_exports'],
                                   c['wrapper_reexports_metalold'], frozenset(c['replaced']))
    doc = {
        'schema': 1,
        'os_build': args.build,
        'arch': 'x86_64',
        'inputs': {
            'cache': {'file': os.path.basename(args.cache), 'uuid': cache_uuid(args.cache)},
            'fs_roots': [s.rpartition('=')[0] or '(system)' for s in args.fs],
            'metalold': {'file': 'MetalOld.dylib', 'sha256': sha256(args.metalold), 'exports': len(c['metalold_exports'])},
            'moraea_wrapper': {'file': 'Metal', 'sha256': sha256(args.wrapper), 'exports': len(c['wrapper_exports']),
                               'reexports_metalold': c['wrapper_reexports_metalold']},
            'metal_exports': len(c['metal_exports']),
            'metal_exports_match_export_trie': c['trie_check'],
            'cache_patch_table_check': c['patch_check'],
            'oclp_artifact': os.path.basename(os.path.normpath(args.oclp_artifact)) if args.oclp_artifact else None,
        },
        'tools': {'ipsw': tool_version([args.ipsw, 'version']), 'dyld_info': 'dyld_info (Xcode)'},
        'importers': {
            'cache': len(c['cache_importers']),
            'disk': len(c['disk_importers']),
            'replaced_by_oclp': sorted(n for n in c['cache_importers'] + c['disk_importers'] if n in c['replaced']),
            'cache_fs_listed_not_scanned': c['cache_fs_listed_not_scanned'],
            'arm64_only_not_counted': sorted(n for n, _ in c['arm64_only']),
        },
        'arm64_only_extra': arm64_extra(c['arm64_only'], symbols, c['metal_exports'], c['metalold_exports'],
                                        c['wrapper_exports']),
        'notes': {
            'exported_by.moraea_wrapper': "the wrapper's own exports; it also re-exports MetalOld, so "
                                          'provided_today = metalold or moraea_wrapper',
            'required': 'referenced by at least one importer the OCLP overlay does not replace',
            'importers.strong': 'importers with a non-weak reference (dyld refuses to load an on-disk image '
                                'whose non-weak import is missing)',
            'importers.outside_gpu_driver_bundles': 'importers that are not /System/Library/Extensions/*.bundle '
                                                    'Metal drivers; none of those loads on an Intel GPU',
            'cache_fs_listed_not_scanned': 'programs the cache has launch info for that have no x86_64 slice on '
                                           'disk (checked by the scan)',
        },
        'summary': summary,
        'errors': c['errors'],
        'symbols': symbols,
    }
    Path(args.out).write_text(json.dumps(doc, indent=1, sort_keys=False) + '\n')
    for kind, s in summary.items():
        if s['required']:
            log(f"{kind}: required {s['required']} (strong {s['required_strong']}), missing from MetalOld "
                f"{s['missing_from_metalold']}, Moraea provides {s['moraea_provides']}, nobody "
                f"{len(s['nobody_provides'])} ({len(s['nobody_provides_outside_gpu_driver_bundles'])} outside "
                f"GPU driver bundles)")
    if c['errors']:
        log(f"{len(c['errors'])} importer(s) failed; see 'errors'")
    return 0


if __name__ == '__main__':
    sys.exit(main())
