"""Method-level coverage for a library a compat library replaces (IO80211, WiFiPeerToPeer, CoreWiFi): the Objective-C
selectors a 27.x build's clients may send to classes the replacement provides (the compat library's stand-ins, OCLP's
wrapper, or for CoreWiFi Tahoe's own library) and that the provided class does not implement.

coverage.py proves every class a client binds exists. A class that exists but lacks a method a 27.x client sends still
raises `unrecognized selector` at run time (WiFiAgent: -[WiFiP2PKeychainAgent activate] on the r43 stand-in). For each
class the build's own LEAF defines and a client binds, a gap is a selector that
- the build's own class (or a superclass inside LEAF) implements,
- a client binding the class names in its selector references (`@selectors refs`),
- the provided class does not implement: it comes from the first library exporting the class symbol (compat, OCLP's
  wrapper, the libraries it re-exports), plus any category the others put on it (OCLP's wrapper adds `(Stub)`
  categories to the re-exported library's classes), walking superclasses, and
- NSObject (libobjc, CoreFoundation, Foundation, categories included) does not answer.

Selector references are not typed, so a gap is a candidate: the client names the selector and binds the class but may
send it to another receiver. Clients are each on-disk Mach-O under the System root binding a class of LEAF (any
architecture: 27.x programs are arm64e and run under hao, against the same x86_64 libraries), and the x86_64 shared
cache as one client: its importers bind LEAF's classes through shared GOTs that do not name the image, so its selectors
are the union over every cached importer of LEAF and its gaps are coarse.

CoreWiFi differs in three ways (check()'s keywords). Its replacement is Tahoe's whole CoreWiFi, whose method lists are
readable, so a gap on a Tahoe class is fixable and an error too, even when only the cache's importers name it
(`answerable`, `strict`). Instances of 27.x CoreWiFi classes
reach clients through CoreWiFi's API without the client binding the class, so every class the build defines is checked
for every client (`every_class`), not only bound ones. And the compat library adds its methods at run time with
class_addMethod (its kMethods table, `added`), which ipsw cannot see; an added method a provider already answers is an
error (class_addMethod would skip it: the entry is dead or the table is stale).

Result: `errors` are gaps on compat stand-ins (or other answerable providers) named by an on-disk client; `reported` are stand-in gaps named only by the
cache union, and gaps on OCLP classes whose method lists can be read; `unverified` are OCLP classes whose own method
list cannot be read offline (OCLP's WiFiPeerToPeerOld.dylib is cut from a 13.7.2 shared cache and its method lists
name selectors relative to that cache's selector base), with the selectors clients name on them.

Host tools: ipsw (dyld macho/imports/patches, macho info), xcrun dyld_info."""
from pathlib import Path
import re
import subprocess

NSOBJECT_IMAGES = ('libobjc.A.dylib', 'CoreFoundation', 'Foundation')
CACHE = 'shared cache'
METHOD = re.compile(r'^([-+])\[([A-Za-z_][\w.$]*)(?:\s*\(\s*[\w.$]*\s*\))?\s+([^\]]+)\];?$')
INTERFACE = re.compile(r'^@interface ([A-Za-z_][\w.$]*)\s*(\(\s*[\w.$]*\s*\))?(?:\s*:\s*([A-Za-z_][\w.$]*))?')
SELREF = re.compile(r'^0x[0-9a-f]+ => 0x[0-9a-f]+: (\S+)$')
CLASS_SYMBOL = re.compile(r'^_OBJC_CLASS_\$_(\S+)$')


def out(*args):
    return subprocess.run(list(map(str, args)), capture_output=True).stdout.decode('utf-8', 'replace')


def parse_classes(text):
    """{class: {'super': name or None, 'defined': bool, 'methods': {'-sel', '+sel'}}} from `ipsw ... --objc` text.
    'defined' is True when the text has the class's own @interface (not only categories on it)."""
    classes = {}

    def entry(name):
        return classes.setdefault(name, {'super': None, 'defined': False, 'methods': set()})
    for line in text.splitlines():
        line = line.strip()
        m = INTERFACE.match(line)
        if m:
            if not m[2]:
                entry(m[1])['defined'] = True
                entry(m[1])['super'] = m[3]
            else:
                entry(m[1])
            continue
        m = METHOD.match(line)
        if m:
            entry(m[2])['methods'].add(m[1] + m[3].strip())
    return classes


def parse_selrefs(text):
    """Selector names in the `@selectors refs` section of `ipsw ... --objc --objc-refs` text."""
    found, inside = set(), False
    for line in text.splitlines():
        if line.startswith('@'):
            inside = line.strip() == '@selectors refs'
            continue
        m = SELREF.match(line.strip()) if inside else None
        if m:
            found.add(m[1])
    return found


def methods_of(classes, name):
    """Every selector CLASS NAME implements in CLASSES, superclasses inside CLASSES included."""
    found, seen = set(), set()
    while name and name in classes and name not in seen:
        seen.add(name)
        found |= classes[name]['methods']
        name = classes[name]['super']
    return found


def answered(providers, name):
    """Selectors class NAME answers across PROVIDERS: at each class of its superclass chain (the chain of the first
    provider defining the class), every provider's methods on that class name (its own list or categories)."""
    found, seen = set(), set()
    while name and name not in seen:
        seen.add(name)
        found |= set().union(*(classes.get(name, {}).get('methods', set()) for _, _, classes in providers))
        name = next((classes[name]['super'] for _, _, classes in providers if classes.get(name, {}).get('defined')),
                    None)
    return found


def assess(build_classes, providers, nsobject, clients, every_class=False):
    """BUILD_CLASSES the build's own LEAF classes (parse_classes); PROVIDERS [(name, exported class names, classes)] in
    resolution order; NSOBJECT the selectors NSObject answers; CLIENTS {client: (bound class names, selector names)}.
    Returns {class: {'provider': name or None, 'readable': bool, 'gaps': {selector: [client, ...]}}} for every bound
    class the build defines, or with EVERY_CLASS every class it defines (a provider then also provides a class it
    defines without exporting it)."""
    report = {}
    for client, (bound, selectors) in sorted(clients.items()):
        for name in sorted(build_classes if every_class else bound):
            if name not in build_classes:
                continue
            exporter = next(((pname, classes) for pname, exported, classes in providers
                             if name in exported or every_class and classes.get(name, {}).get('defined')), None)
            known = answered(providers, name)
            readable = bool(exporter) and exporter[1].get(name, {}).get('defined', False)
            entry = report.setdefault(name, {'provider': exporter[0] if exporter else None, 'readable': readable,
                                             'gaps': {}})
            for selector in sorted(methods_of(build_classes, name)):
                if selector[1:] in selectors and selector not in nsobject and selector not in known:
                    entry['gaps'].setdefault(selector, []).append(client)
    return report


def classify(report, leaf, answerable=('compat',), strict=False):
    """(errors, reported, unverified) texts for assess()'s REPORT; a gap is an error when its class comes from an
    ANSWERABLE provider and an on-disk client names it, or with STRICT any client (the cache union too)."""
    errors, reported, unverified = [], [], []
    for name, entry in sorted(report.items()):
        if entry['provider'] and not entry['readable']:
            if entry['gaps']:
                unverified.append(f'{entry["provider"]} {name}: own methods unreadable offline; clients name '
                                  + ', '.join(sorted(entry['gaps'])))
            continue
        for selector, users in sorted(entry['gaps'].items()):
            text = f'{selector[0]}[{name} {selector[1:]}] from {entry["provider"] or "nothing"} (named by {", ".join(users)})'
            if entry['provider'] in answerable and (strict or any(user != CACHE for user in users)):
                errors.append(f'MISSING method: {text}; the build\'s {leaf} implements it')
            else:
                reported.append(text)
    return errors, reported, unverified


def cache_classes(cache, image):
    return parse_classes(out('ipsw', 'dyld', 'macho', cache, image, '--objc', '--no-color'))


def nsobject_selectors(cache):
    found = set()
    for image in NSOBJECT_IMAGES:
        found |= cache_classes(cache, image).get('NSObject', {'methods': set()})['methods']
    return found


def cache_client(cache, leaf):
    """(bound class names, selector names, importer paths) for the x86_64 cache: LEAF's classes bound from outside LEAF
    (patch table entries not attributed to LEAF itself) and the selectors of every cached image importing LEAF."""
    bound, symbol = set(), None
    for line in out('ipsw', 'dyld', 'patches', cache, '-i', leaf, '--no-color').splitlines():
        if line.startswith('0x'):
            m = re.match(r'^0x[0-9a-f]+: _OBJC_CLASS_\$_(\S+)$', line)
            symbol = m[1] if m else None
            continue
        parts = line.split()
        if symbol and len(parts) >= 2 and parts[0].startswith('0x') and parts[-1] != leaf:
            bound.add(symbol)
    text = out('ipsw', 'dyld', 'imports', cache, leaf, '--no-color')
    images = []
    for line in text.split('In DSC (Dylibs)', 1)[-1].splitlines() if 'In DSC (Dylibs)' in text else ():
        if line.startswith('In '):
            break
        if line.startswith('/'):
            images.append(line.strip())
    selectors = set()
    for image in images:
        selectors |= parse_selrefs(out('ipsw', 'dyld', 'macho', cache, image, '--objc', '--objc-refs', '--no-color'))
    return bound, selectors, images


def exported_classes(path):
    names = set()
    for line in out('xcrun', 'dyld_info', '-exports', path).splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0].startswith('0x') and 're-export' not in line:
            m = CLASS_SYMBOL.match(parts[1])
            if m:
                names.add(m[1])
    return names


def provider(name, path):
    return (name, exported_classes(path), parse_classes(out('ipsw', 'macho', 'info', path, '--objc', '--no-color')))


def check(cache, root, oclp, compat, leaf, reexported, disk, wrapper='OCLP', added=None, every_class=False,
          answerable=('compat',), strict=False):
    """Method coverage for one build. DISK is coverage.disk_imports() for LEAF; OCLP the folder holding the replaced
    library (WRAPPER names whose: OCLP's wrapper, or Tahoe's CoreWiFi); ADDED {class: {'-selector'}} the methods the
    compat library adds at run time. Returns a record for build.json whose 'errors' are unanswered methods (on an
    ANSWERABLE provider's class) named by an on-disk client (STRICT: by any client), and added methods a provider
    already answers."""
    build_classes = cache_classes(cache, leaf)
    if not build_classes:
        return dict(classes=0, errors=[], reported=[], unverified=[])
    providers = [provider('compat', compat), provider(f'{wrapper} {leaf}', Path(oclp)/leaf)] + [
        provider(f'{wrapper} {name}', Path(oclp)/name) for name in reexported]
    dead = []
    for name, selectors in sorted((added or {}).items()):
        dead += [f'ALREADY ANSWERED: compat adds {s[0]}[{name} {s[1:]}], which {name} already has; class_addMethod '
                 'skips it' for s in sorted(selectors & answered(providers, name))]
        providers[0][2].setdefault(name, {'super': None, 'defined': False, 'methods': set()})['methods'] |= selectors
    bound, selectors, images = cache_client(cache, leaf)
    clients = {CACHE: (bound, selectors)}
    per_client = {}
    for symbol, uses in disk.items():
        m = CLASS_SYMBOL.match(symbol)
        for client, arch, _ in uses if m or every_class else ():
            names = per_client.setdefault((client, arch), set())
            if m:
                names.add(m[1])
    for (client, arch), names in sorted(per_client.items()):
        text = out('ipsw', 'macho', 'info', Path(root)/client, '--arch', arch, '--objc', '--objc-refs', '--no-color')
        clients[f'{client} [{arch}]'] = (names, parse_selrefs(text))
    report = assess(build_classes, providers, nsobject_selectors(cache), clients, every_class)
    errors, reported, unverified = classify(report, leaf, answerable, strict)
    return dict(classes=len(build_classes), cache_importers=len(images), clients=sorted(clients),
                added=sorted(f'-[{c} {s[1:]}]' for c, sels in (added or {}).items() for s in sels),
                errors=dead + errors, reported=reported, unverified=unverified)
