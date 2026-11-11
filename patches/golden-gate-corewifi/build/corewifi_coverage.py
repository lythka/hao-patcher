"""Coverage of the Tahoe CoreWiFi override for one 27.x build: does every 27.x client of CoreWiFi find what it needs
once CoreWiFi is the compat library re-exporting Tahoe's CoreWiFi (CoreWiFiTahoe.dylib)?

Symbols. Clients are the x86_64 cache's importers of CoreWiFi (its dyld patch table, which is what dyld repoints to
an on-disk override), every on-disk Mach-O of the build that links CoreWiFi (any architecture; 27.x programs run
under hao against the same x86_64 libraries) except those the image replaces, and the programs the image installs
from elsewhere (Tahoe's airportd, OCLP's wifip2pd, ...: EXTRA). Each bound symbol must resolve to the compat
library or CoreWiFiTahoe.dylib, in that order; a symbol the build's CoreWiFi exports as code must not resolve to data.
Weak imports may stay unresolved.

Methods. For every class of the build's own CoreWiFi, every selector that class implements (superclasses inside
CoreWiFi included) which some client names in its selector references (the cache's importers pooled) is a gap
unless the provided class answers it: Tahoe's class and its Tahoe superclasses, with the compat library's
categories on any of them, or NSObject. Instances of 27.x CoreWiFi classes reach clients through CoreWiFi's API, so
every class is checked, not only the ones clients bind; the selectors are untyped, so a gap is a candidate. Every
gap is an error until the compat library answers it. A class Tahoe lacks and no client binds is recorded as absent
(CoreWiFiTahoe never creates its instances). A compat category method that Tahoe's class chain already implements
is an error: the category would replace Tahoe's implementation.

Re-exported clients of the dependencies (IO80211, WiFiPeerToPeer: OCLP's wrappers and this image's compat
libraries) are checked by environment(): every symbol CoreWiFiTahoe.dylib binds must be exported by the build's
x86_64 cache or, for the libraries the image overrides on disk, by those files.

Host tools: ipsw (dyld patches/imports/macho/extract, macho info), xcrun dyld_info, otool, nm, dwarfdump."""
from pathlib import Path
import re
import struct
import subprocess
import sys

HERE = Path(__file__).resolve().parent
TOP = HERE.parents[2]
IO80211 = TOP/'patches/golden-gate-userspace/src/io80211'
sys.path.insert(0, str(TOP/'tools/cache-dylib'))
import dsc  # noqa: E402


def _load(name, path):
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


# golden-gate-userspace's IO80211/WiFiPeerToPeer checks, reused: exports, disk_imports, cache_imports, target_kinds
# (coverage.py) and parse_classes, parse_selrefs, cache_classes, cache_client, nsobject_selectors (methods.py)
methods = _load('methods', IO80211/'methods.py')
io_coverage = _load('io80211_coverage', IO80211/'coverage.py')

LEAF = 'CoreWiFi'
CACHE = methods.CACHE


def out(*args):
    return subprocess.run(list(map(str, args)), capture_output=True).stdout.decode('utf-8', 'replace')


TABLE_ENTRY = re.compile(r'^\s*\{"(\w+)", "([\w:]+)", "([^"]+)", \(IMP\)(\w+)\},$', re.M)


def compat_table(source):
    """[(class, selector, types, imp)] of the compat library's kMethods table (corewifi_compat.m): the instance
    methods its constructor adds to Tahoe's classes."""
    return TABLE_ENTRY.findall(Path(source).read_text())


def cache_imports(cache):
    """(bound, self_only): CoreWiFi symbols the x86_64 cache's patch table lists with a use outside CoreWiFi, and
    those whose only uses are CoreWiFi's own (they need no answer once CoreWiFi is replaced)."""
    bound, self_only, symbol, users = set(), set(), None, set()

    def flush():
        if symbol:
            (bound if users - {LEAF} else self_only).add(symbol)
    for line in out('ipsw', 'dyld', 'patches', cache, '-i', LEAF, '--no-color').splitlines():
        m = re.match(r'^0x[0-9a-f]+: (\S+)$', line)
        if m:
            flush()
            symbol, users = m[1], set()
            continue
        parts = line.split()
        if symbol and len(parts) >= 2 and parts[0].startswith('0x') and parts[1] in ('PATCH', 'GOT'):
            # a PATCH line names the image using the slot; a GOT line is a uniqued GOT slot other images share
            user = parts[-1] if parts[1] == 'PATCH' and not parts[-1].startswith('(') else 'uniqued GOT'
            users.add(user)
    flush()
    return bound, self_only


def objc_classes(path):
    return methods.parse_classes(out('ipsw', 'macho', 'info', path, '--objc', '--no-color'))


def extra_imports(extra):
    """{symbol: {(label, arch, weak)}} for EXTRA {label: Mach-O path}: programs the image installs from elsewhere."""
    found = {}
    for label, path in sorted(extra.items()):
        for arch in io_coverage.out('lipo', '-archs', path).split():
            for line in io_coverage.out('xcrun', 'dyld_info', '-arch', arch, '-imports', path).splitlines():
                if f'(from {LEAF})' in line:
                    found.setdefault(line.split()[1], set()).add((label, arch, 'weak-import' in line))
    return found


def assess_symbols(imports, kinds, providers):
    resolved, errors = {}, []
    for symbol in sorted(imports):
        clients = sorted(imports[symbol])
        provider = next(((name, table[symbol]) for name, table in providers if symbol in table), None)
        if provider is None:
            if all(c.endswith(' (weak)') for c in clients):
                resolved[symbol] = 'weak, unresolved'
                continue
            errors.append(f'MISSING/unsupported: {symbol} (bound by {", ".join(clients)}) is exported by neither the '
                          'compat library nor Tahoe\'s CoreWiFi')
            continue
        name, segment = provider
        if kinds.get(symbol) == 'code' and segment != '__TEXT':
            errors.append(f'MISSING/unsupported: {symbol} (bound by {", ".join(clients)}) is code in this build\'s '
                          f'CoreWiFi but {name} exports it from {segment}')
            continue
        resolved[symbol] = name
    return resolved, errors


def provided(tahoe, compat, name):
    """Selectors class NAME answers once the override is in place: Tahoe's class and Tahoe superclasses, each with the
    compat library's methods (categories, or the compat class itself) on the same class name. None if neither library
    has the class."""
    if name not in tahoe and not compat.get(name, {}).get('defined'):
        return None
    found, seen = set(), set()
    while name and name not in seen:
        seen.add(name)
        found |= tahoe.get(name, {}).get('methods', set()) | compat.get(name, {}).get('methods', set())
        name = tahoe[name]['super'] if name in tahoe else compat.get(name, {}).get('super')
    return found


def assess_methods(build, tahoe, compat, nsobject, clients):
    """BUILD/TAHOE/COMPAT parse_classes() of the build's CoreWiFi, Tahoe's and the compat library; CLIENTS {client:
    (bound class names, selector names)}. Returns (gaps {(class, selector): [client]}, absent {class: [selector]},
    shadowed [(class, selector)])."""
    gaps, absent = {}, {}
    for client, (bound, selectors) in sorted(clients.items()):
        for name in sorted(build):
            wanted = [s for s in sorted(methods.methods_of(build, name)) if s[1:] in selectors and s not in nsobject]
            if not wanted:
                continue
            answers = provided(tahoe, compat, name)
            if answers is None:
                if name not in bound:
                    absent.setdefault(name, set()).update(wanted)
                continue
            for selector in wanted:
                if selector not in answers:
                    gaps.setdefault((name, selector), []).append(client)
    shadowed = []
    for name, entry in sorted(compat.items()):
        if entry.get('defined'):
            continue
        chain, seen, cls = set(), set(), name
        while cls in tahoe and cls not in seen:
            seen.add(cls)
            chain |= tahoe[cls]['methods']
            cls = tahoe[cls]['super']
        shadowed += [(name, s) for s in sorted(entry['methods'] & chain)]
    return gaps, {k: sorted(v) for k, v in absent.items()}, shadowed


def check(cache, root, compat, tahoe, source, extra=None, replaced=()):
    """One build: CACHE its x86_64 dyld cache file, ROOT its System volume, COMPAT and TAHOE the two libraries, SOURCE
    the compat library's source (its kMethods table), EXTRA {label: (path, built against Tahoe's CoreWiFi)} programs
    the image adds, REPLACED on-disk clients (relative to ROOT) the image replaces. Programs built against Tahoe's
    CoreWiFi join the symbol check only: what they send, Tahoe's CoreWiFi answers by construction."""
    import tempfile
    with tempfile.TemporaryDirectory(prefix='corewifi-coverage-') as workspace:
        kinds, uuid = io_coverage.target_kinds(cache, workspace, LEAF)
    in_cache, self_only = cache_imports(cache)
    imports = {symbol: {CACHE} for symbol in in_cache}
    extra = extra or {}
    paths = {label: path for label, (path, _) in extra.items()}
    disk = io_coverage.disk_imports(root, LEAF, replaced)
    disk = {s: {u for u in uses if not u[0].startswith(f'System/Library/PrivateFrameworks/{LEAF}.framework/')}
            for s, uses in disk.items()}
    added = extra_imports(paths)
    for table in (disk, added):
        for symbol, uses in table.items():
            imports.setdefault(symbol, set()).update(f'{c} [{a}]' + (' (weak)' if w else '') for c, a, w in uses)
    imports = {s: c for s, c in imports.items() if c}
    providers = [('compat', io_coverage.exports(compat)), ('Tahoe CoreWiFi', io_coverage.exports(tahoe))]
    resolved, errors = assess_symbols(imports, kinds, providers)

    build = methods.cache_classes(cache, LEAF)
    tahoe_classes, compat_classes = objc_classes(tahoe), objc_classes(compat)
    entries = compat_table(source)
    for name, selector, _, _ in entries:
        compat_classes.setdefault(name, {'super': None, 'defined': False, 'methods': set()})['methods'].add('-' + selector)
    bound, selectors, importers = methods.cache_client(cache, LEAF)
    clients = {CACHE: (bound, selectors)}
    per_client = {}
    senders = {s: {u for u in uses if not extra[u[0]][1]} for s, uses in added.items()}
    for imported, base in ((disk, Path(root)), (senders, None)):
        for symbol, uses in imported.items():
            m = methods.CLASS_SYMBOL.match(symbol)
            for client, arch, _ in uses if m else ():
                path = base/client if base else Path(paths[client])
                per_client.setdefault((client, arch, path), set()).add(m[1])
    users = {(c, a, p) for imported, base in ((disk, Path(root)), (senders, None)) for uses in imported.values()
             for c, a, _ in uses for p in [base/c if base else Path(paths[c])]}
    for client, arch, path in sorted(users):
        text = out('ipsw', 'macho', 'info', path, '--arch', arch, '--objc', '--objc-refs', '--no-color')
        clients[f'{client} [{arch}]'] = (per_client.get((client, arch, path), set()), methods.parse_selrefs(text))
    gaps, absent, shadowed = assess_methods(build, tahoe_classes, compat_classes, methods.nsobject_selectors(cache),
                                            clients)
    for (name, selector), who in sorted(gaps.items()):
        errors.append(f'MISSING method: {selector[0]}[{name} {selector[1:]}] (named by {", ".join(who)}); the build\'s '
                      'CoreWiFi implements it, Tahoe\'s and the compat library do not')
    for name, selector in shadowed:
        errors.append(f'SHADOWS Tahoe: compat category method {selector[0]}[{name} {selector[1:]}] replaces Tahoe\'s')
    return dict(corewifi_uuid=uuid, cache_symbols=len(in_cache), cache_self_only=sorted(self_only),
                cache_importers=importers, disk_clients=sorted({c for uses in disk.values() for c, _, _ in uses}),
                extra_clients={label: dict(path=str(path), built_against_tahoe=t) for label, (path, t) in extra.items()},
                replaced=sorted(replaced), compat_methods=[f'-[{c} {s}] {t}' for c, s, t, _ in entries],
                symbols={s: resolved.get(s, 'ERROR') for s in sorted(imports)},
                methods=dict(classes=len(build), tahoe_classes=len(tahoe_classes), clients=sorted(clients),
                             answered_by_compat=sorted(f'{s[0]}[{c} {s[1:]}]' for c, e in compat_classes.items()
                                                       for s in e['methods']),
                             absent_classes=absent),
                errors=errors)


# ----- environment: what CoreWiFiTahoe.dylib binds, against the build

def file_exports(path):
    """{symbol: flags/value} and re-exported install names of an on-disk Mach-O (x86_64 slice)."""
    data = Path(path).read_bytes()
    magic, = struct.unpack_from('>I', data)
    if magic in (0xCAFEBABE, 0xCAFEBABF):
        count, = struct.unpack_from('>I', data, 4)
        wide = magic == 0xCAFEBABF
        for i in range(count):
            if wide:
                cpu, _, offset, size, _, _ = struct.unpack_from('>iiQQII', data, 8 + i*32)
            else:
                cpu, _, offset, size, _ = struct.unpack_from('>iiIII', data, 8 + i*20)
            if cpu == 0x01000007:
                data = data[offset:offset + size]
                break
        else:
            raise ValueError(f'{path}: no x86_64 slice')
    _, _, _, _, ncmds, _, _, _ = struct.unpack_from('<IiiIIIII', data)
    at, trie, reexports, dylibs = 32, None, [], []
    for _ in range(ncmds):
        cmd, size = struct.unpack_from('<II', data, at)
        if cmd == dsc.LC_DYLD_EXPORTS_TRIE:
            off, length = struct.unpack_from('<II', data, at + 8)
            trie = data[off:off + length]
        elif cmd in (dsc.LC_DYLD_INFO, dsc.LC_DYLD_INFO_ONLY):
            off, length = struct.unpack_from('<II', data, at + 40)
            trie = data[off:off + length] if length else trie
        elif cmd in dsc.DYLIB_COMMANDS:
            name_off, = struct.unpack_from('<I', data, at + 8)
            name = data[at + name_off:at + size].split(b'\0', 1)[0].decode()
            dylibs.append(name)
            if cmd == dsc.LC_REEXPORT_DYLIB:
                reexports.append(name)
        at += size
    return (dsc.parse_trie(trie) if trie else {}), reexports, dylibs


def environment(binds, cache_dir, overrides, weak=(), arch='x86_64'):
    """BINDS {install name: [symbols]} (cache_dylib's report), CACHE_DIR the build's x86_64 cache folder, OVERRIDES
    {install name: on-disk file the image installs there}, WEAK the weakly linked install names (their binds are weak
    imports: a missing one is set to NULL). Returns (missing [text], weak_missing [text], where {install name: ...})."""
    cache = dsc.Cache(cache_dir, arch)
    files = {}

    def library(name):
        if name in overrides:
            if name not in files:
                files[name] = file_exports(overrides[name])
            return ('file', files[name])
        try:
            image = cache.image(name)
        except LookupError:
            return None
        return ('cache', (image.exports(), [p for c, p in image.dylibs if c == dsc.LC_REEXPORT_DYLIB],
                          [p for c, p in image.dylibs]))

    def has(name, symbol, seen=()):
        if name in seen:
            return False
        lib = library(name)
        if lib is None:
            return False
        exports, reexports, dylibs = lib[1]
        if symbol in exports:
            flags, value = exports[symbol]
            if flags & dsc.EXPORT_SYMBOL_FLAGS_REEXPORT:
                ordinal, other = value
                return 0 < ordinal <= len(dylibs) and has(dylibs[ordinal - 1], other, seen + (name,))
            return True
        return any(has(r, symbol, seen + (name,)) for r in reexports)
    try:
        missing, weak_missing, where = [], [], {}
        for name, symbols in sorted(binds.items()):
            lib = library(name)
            where[name] = 'missing' if lib is None else ('on disk' if lib[0] == 'file' else 'x86_64 cache')
            for symbol in symbols:
                if not has(name, symbol):
                    (weak_missing if name in weak else missing).append(f'{name}: {symbol}')
        return missing, weak_missing, where
    finally:
        cache.close()
