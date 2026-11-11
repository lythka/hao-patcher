"""Check that every IO80211 symbol a 27.x build's clients bind is answered correctly once the compat library
replaces IO80211 and re-exports OCLP's wrapper (IO80211OCLP.dylib, which re-exports IO80211Old.dylib). The same
check serves any other library a compat library replaces (src/wifip2p: OCLP's WiFiPeerToPeer wrapper; src/corewifi:
Tahoe's CoreWiFi, WRAPPER 'Tahoe'): pass LEAF and the wrapper's re-exported libraries to check().

Clients: the x86_64 shared cache's own users of IO80211 (its dyld patch table, which lists every symbol bound in the
cache and is what dyld repoints when a file overrides a cached dylib) and every Mach-O under the System root that
links IO80211 (each architecture's imports). Each symbol resolves to the first library exporting it: compat, then the
wrapper, then IO80211Old. A symbol nothing exports, or one the build's IO80211 exports as code that resolves to data
(the wrapper's CFString-pointer exports), is an error.

Then the method pass (methods.py) checks the Objective-C methods clients name on the classes the replacement provides;
its errors join this check's.

Host tools: ipsw (dyld patches, dyld extract, dyld macho, macho info), xcrun dyld_info, otool, nm."""
from pathlib import Path
import re
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
import methods  # noqa: E402

LEAF = 'IO80211'


def install_name(leaf):
    return f'/System/Library/PrivateFrameworks/{leaf}.framework/Versions/A/{leaf}\0'.encode()
MACHO = (b'\xcf\xfa\xed\xfe', b'\xca\xfe\xba\xbe', b'\xca\xfe\xba\xbf')


def out(*args):
    return subprocess.run(list(map(str, args)), check=True, capture_output=True, text=True).stdout


def exports(path):
    """{symbol: segment name} of a Mach-O's export trie (re-exports left out: resolution continues past them)."""
    segments = []
    for block in out('otool', '-l', path).split('Load command')[1:]:
        if 'cmd LC_SEGMENT_64' not in block:
            continue
        field = dict(re.findall(r'^\s*(segname|vmaddr|vmsize)\s+(\S+)', block, re.M))
        segments.append((field['segname'], int(field['vmaddr'], 16), int(field['vmsize'], 16)))
    base = next(vm for name, vm, _ in segments if name == '__TEXT')
    found = {}
    for line in out('xcrun', 'dyld_info', '-exports', path).splitlines():
        parts = line.split()
        if len(parts) < 2 or not parts[0].startswith('0x') or 're-export' in line:
            continue
        address = base + int(parts[0], 16)
        found[parts[1]] = next((name for name, vm, size in segments if vm <= address < vm + size), '?')
    return found


def target_kinds(cache, workspace, leaf=LEAF):
    """{symbol: 'code' or 'data'} for the build's own LEAF library in CACHE."""
    out('ipsw', 'dyld', 'extract', cache, leaf, '-o', workspace, '--no-color')
    image = next(p for p in Path(workspace).rglob(leaf) if p.is_file())
    kinds = {}
    for line in out('nm', '-gU', image).splitlines():
        parts = line.split()
        if len(parts) == 3:
            kinds[parts[2]] = 'code' if parts[1] in 'Tt' else 'data'
    return kinds, out('dwarfdump', '--uuid', image).split()[1]


def cache_imports(cache, leaf=LEAF):
    """Symbols of LEAF bound by images in CACHE (its patch table), leaving out those whose only uses are LEAF's own
    (replacing LEAF removes those uses). A uniqued GOT use counts as foreign: it does not name the image."""
    bound, symbol, users = set(), None, set()
    for line in out('ipsw', 'dyld', 'patches', cache, '-i', leaf, '--no-color').splitlines() + ['0x0: ']:
        m = re.match(r'^0x[0-9a-f]+: (\S*)$', line)
        if m:
            if symbol and users - {leaf}:
                bound.add(symbol)
            symbol, users = m[1], set()
            continue
        parts = line.split()
        if symbol and len(parts) >= 2 and parts[0].startswith('0x') and parts[1] in ('PATCH', 'GOT'):
            users.add(parts[-1] if parts[1] == 'PATCH' and not parts[-1].startswith('(') else 'uniqued GOT')
    return bound


def disk_imports(root, leaf=LEAF, replaced=()):
    """{symbol: {(client, arch, weak)}} for every Mach-O under ROOT that links LEAF, except LEAF's framework and the
    REPLACED clients (paths relative to ROOT that OCLP overwrites in the image, so the build's own copy never runs)."""
    found, name = {}, install_name(leaf)
    for top in ('System', 'usr', 'Library', 'Applications', 'sbin', 'bin'):
        for path in sorted((Path(root)/top).rglob('*')):
            if path.is_symlink() or not path.is_file() or f'/{leaf}.framework/' in str(path):
                continue
            try:
                with path.open('rb') as stream:
                    if stream.read(4) not in MACHO:
                        continue
                    if name not in path.read_bytes():
                        continue
            except OSError:
                continue
            client = str(path.relative_to(root))
            if client in replaced:
                continue
            for arch in out('lipo', '-archs', path).split():
                for line in out('xcrun', 'dyld_info', '-arch', arch, '-imports', path).splitlines():
                    if f'(from {leaf})' in line:
                        found.setdefault(line.split()[1], set()).add((client, arch, 'weak-import' in line))
    return found


def assess(imports, kinds, providers, leaf=LEAF, wrapper='OCLP'):
    """IMPORTS {symbol: [client, ...]} (a client is a string; weak ones end in ' (weak)'), KINDS the build's
    LEAF {symbol: 'code'|'data'}, PROVIDERS [(name, {symbol: segment})] in resolution order.
    Returns (resolved {symbol: provider}, errors [text])."""
    resolved, errors = {}, []
    for symbol in sorted(imports):
        clients = sorted(imports[symbol])
        provider = next(((name, table[symbol]) for name, table in providers if symbol in table), None)
        if provider is None:
            if all(c.endswith(' (weak)') for c in clients):
                resolved[symbol] = 'weak, unresolved'
                continue
            errors.append(f'MISSING/unsupported: {symbol} (bound by {", ".join(clients)}) is provided by neither the '
                          f'compat library nor {wrapper}\'s {leaf}')
            continue
        name, segment = provider
        if kinds.get(symbol) == 'code' and segment != '__TEXT':
            errors.append(f'MISSING/unsupported: {symbol} (bound by {", ".join(clients)}) is code in this build\'s '
                          f'{leaf} but {name} exports it from {segment}')
            continue
        resolved[symbol] = name
    return resolved, errors


def check(cache, root, oclp, compat, leaf=LEAF, reexported=('IO80211Old.dylib',), replaced=(), wrapper='OCLP',
          **method_options):
    """Run every step for one build; returns a record for build.json, with 'errors' empty when coverage holds.
    OCLP is the folder holding the replaced LEAF (OCLP's wrapper, or with WRAPPER 'Tahoe' Tahoe's library) and the
    REEXPORTED libraries it re-exports, in order; REPLACED the on-disk clients the image overwrites (see disk_imports).
    METHOD_OPTIONS go to methods.check (added, every_class, answerable)."""
    with tempfile.TemporaryDirectory(prefix=f'{leaf.lower()}-coverage-') as workspace:
        kinds, uuid = target_kinds(cache, workspace, leaf)
    in_cache = cache_imports(cache, leaf)
    imports = {symbol: {'shared cache'} for symbol in in_cache}
    disk = disk_imports(root, leaf, replaced)
    for symbol, uses in disk.items():
        imports.setdefault(symbol, set()).update(f'{c} [{a}]' + (' (weak)' if w else '') for c, a, w in uses)
    providers = [('compat', exports(compat)), (f'{wrapper} {leaf}', exports(Path(oclp)/leaf))] + [
        (f'{wrapper} {name}', exports(Path(oclp)/name)) for name in reexported]
    resolved, errors = assess(imports, kinds, providers, leaf, wrapper)
    method_record = methods.check(cache, root, oclp, compat, leaf, reexported, disk, wrapper, **method_options)
    return dict(**{f'{leaf.lower()}_uuid': uuid}, cache_symbols=len(in_cache), disk_clients=sorted(
                    {c for uses in disk.values() for c, _, _ in uses}),
                **{'replaced_by_oclp' if wrapper == 'OCLP' else 'replaced': sorted(replaced)},
                symbols={s: resolved.get(s, 'ERROR') for s in sorted(imports)}, methods=method_record,
                errors=errors + method_record['errors'])
