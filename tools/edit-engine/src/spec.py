"""Edit-spec format 1: load, validate and describe one declared file edit (patches/NAME/edits/*.json).

Self-contained (standard library only, no sibling imports) so pipeline/patches.py can load it with runpy under
python -I. The format is documented in tools/edit-engine/README.md.
"""
import json
import re

FORMAT = 1
ORIGINS = ('apple', 'tahoe', 'oclp', 'hao')
APPLIED_BY = ('installer', 'opencore', 'kext:IntelCommpage')
PRESERVE = {'identifier', 'entitlements', 'requirements', 'flags', 'runtime'}
KINDS = ('literal', 'matcher', 'op')
# Ops the engine applies, with their argument types and required arguments.
OPS = {
    'macho.add-dylib': ({'path': str, 'weak': bool, 'timestamp': int, 'current_version': int, 'compat_version': int},
                        {'path'}),
    'plist.set': ({'key': list, 'value': object}, {'key', 'value'}),
}
# Ops that are declared here but carried out by their own tool; the engine refuses to apply them.
EXTERNAL_OPS = {
    'metallib.rewrite': 'cache/metallib',
    'kc.link': "Tahoe's kmutil",
    'cryptex.build': 'the cryptex builder',
    'hao.embed-stub': 'the hao-runtime installer',
}
TOP_KEYS = {'format', 'path', 'arch', 'origin', 'applied_by', 'edits', 'sign', 'expect'}
COMMON_KEYS = {'id', 'kind', 'reason', 'evidence', 'asm'}
KIND_KEYS = {
    'literal': {'find', 'replace', 'mask', 'replace_mask', 'count', 'at', 'base', 'limit', 'section'},
    'matcher': {'fn', 'resolved'},
    'op': {'op', 'args'},
}
ID = re.compile(r'[a-z0-9][a-z0-9-]*\Z')
SHA = re.compile(r'[0-9a-f]{64}\Z')
FN = re.compile(r'[A-Za-z0-9_./-]+\.py:[A-Za-z_][A-Za-z0-9_]*\Z')


def load(path):
    with open(path) as f:
        return json.load(f)


def hexbytes(text):
    """'B0 01 0f' -> b'\\xb0\\x01\\x0f'. Whitespace is ignored; anything else must be hex digit pairs."""
    if not isinstance(text, str):
        raise ValueError('expected a hex string, got %r' % (text,))
    clean = re.sub(r'\s+', '', text)
    if not clean or len(clean) % 2 or not re.fullmatch(r'[0-9A-Fa-f]+', clean):
        raise ValueError('not hex bytes: %r' % text)
    return bytes.fromhex(clean)


def offset(value):
    """A file offset written as an integer or a "0x..." string."""
    if isinstance(value, bool):
        raise ValueError('not an offset: %r' % (value,))
    if isinstance(value, int) and value >= 0:
        return value
    if isinstance(value, str) and re.fullmatch(r'0x[0-9A-Fa-f]+', value):
        return int(value, 16)
    raise ValueError('not an offset (integer or "0x..."): %r' % (value,))


def spaced(data):
    return ' '.join('%02X' % b for b in data)


def _relative_path(p):
    return isinstance(p, str) and p and not p.startswith('/') and '\0' not in p and '..' not in p.split('/')


def validate(spec):
    """Problems with SPEC, one string each (empty: it follows format 1)."""
    bad = []
    if not isinstance(spec, dict):
        return ['spec must be a JSON object']
    unknown = set(spec) - TOP_KEYS
    if unknown:
        bad.append('unknown keys %s' % ', '.join(sorted(unknown)))
    if spec.get('format') != FORMAT:
        bad.append('format must be %d' % FORMAT)
    if not _relative_path(spec.get('path')):
        bad.append('path must be a relative path on the installed system')
    if 'arch' in spec and spec['arch'] != 'x86_64':
        bad.append('arch must be "x86_64"')
    origin = spec.get('origin')
    if not isinstance(origin, dict) or origin.get('kind') not in ORIGINS:
        bad.append('origin must be an object whose kind is one of %s' % ', '.join(ORIGINS))
    elif set(origin) - {'kind', 'name', 'row', 'note'} or not all(isinstance(v, str) for v in origin.values()):
        bad.append('origin takes kind, name, row and note (strings)')
    if spec.get('applied_by', 'installer') not in APPLIED_BY:
        bad.append('applied_by must be one of %s' % ', '.join(APPLIED_BY))
    sign = spec.get('sign')
    if sign is not None and (not isinstance(sign, dict) or sign.get('mode') != 'adhoc'
                             or set(sign) - {'mode', 'identifier', 'preserve'}
                             or not isinstance(sign.get('identifier', ''), str)
                             or not isinstance(sign.get('preserve', []), list)
                             or set(sign.get('preserve', [])) - PRESERVE
                             or ('identifier' in sign and 'identifier' in sign.get('preserve', []))):
        bad.append('sign must be null or {"mode": "adhoc", "identifier": NAME | "preserve": [%s]} (not both an '
                   'identifier and preserving it)' % ', '.join(sorted(PRESERVE)))
    expect = spec.get('expect', {})
    if not isinstance(expect, dict) or not all(SHA.match(k) and isinstance(v, str) and SHA.match(v)
                                               for k, v in expect.items()):
        bad.append('expect maps an input sha256 to the output sha256 (lower-case hex)')
    edits = spec.get('edits')
    if not isinstance(edits, list) or not edits:
        bad.append('edits must be a non-empty list')
        return bad
    seen, plist_ops, byte_edits = set(), 0, 0
    for i, e in enumerate(edits, 1):
        where = 'edit %d' % i
        if not isinstance(e, dict):
            bad.append('%s: must be an object' % where)
            continue
        eid = e.get('id')
        if not (isinstance(eid, str) and ID.match(eid)):
            bad.append('%s: id must be lower-case letters, digits and -' % where)
        else:
            where = eid
            if eid in seen:
                bad.append('%s: duplicate id' % eid)
            seen.add(eid)
        kind = e.get('kind')
        if kind not in KINDS:
            bad.append('%s: kind must be one of %s' % (where, ', '.join(KINDS)))
            continue
        unknown = set(e) - COMMON_KEYS - KIND_KEYS[kind]
        if unknown:
            bad.append('%s: unknown keys %s' % (where, ', '.join(sorted(unknown))))
        if not (isinstance(e.get('reason'), str) and e['reason'].strip()):
            bad.append('%s: reason is required' % where)
        if 'evidence' in e and not isinstance(e['evidence'], str):
            bad.append('%s: evidence must be a string' % where)
        asm = e.get('asm')
        if asm is not None and (not isinstance(asm, dict) or set(asm) != {'before', 'after'}
                                or not all(isinstance(v, str) for v in asm.values())):
            bad.append('%s: asm must be {"before": TEXT, "after": TEXT}' % where)
        if kind == 'literal':
            byte_edits += 1
            bad += ['%s: %s' % (where, p) for p in _literal_problems(e)]
        elif kind == 'matcher':
            byte_edits += 1
            bad += ['%s: %s' % (where, p) for p in _matcher_problems(e)]
        else:
            op = e.get('op')
            if op == 'plist.set':
                plist_ops += 1
            else:
                byte_edits += 1
            bad += ['%s: %s' % (where, p) for p in _op_problems(e)]
    if plist_ops and byte_edits:
        bad.append('plist.set edits cannot be mixed with byte edits in one spec')
    return bad


def _literal_problems(e):
    bad = []
    try:
        find, replace = hexbytes(e.get('find')), hexbytes(e.get('replace'))
    except ValueError as x:
        return ['find and replace must be hex bytes (%s)' % x]
    if len(find) != len(replace):
        bad.append('find and replace must be the same length')
    for key, length in (('mask', len(find)), ('replace_mask', len(replace))):
        if key in e:
            try:
                if len(hexbytes(e[key])) != length:
                    bad.append('%s must be as long as %s' % (key, 'find' if key == 'mask' else 'replace'))
            except ValueError as x:
                bad.append('%s: %s' % (key, x))
    if 'mask' not in e and 'replace_mask' not in e and find == replace:
        bad.append('find and replace are the same')
    count = e.get('count', 1)
    if isinstance(count, bool) or not isinstance(count, int) or count < 1:
        bad.append('count must be a number >= 1')
    anchors = [k for k in ('at', 'base', 'section') if k in e]
    if len(anchors) != 1:
        bad.append('give exactly one of at (file offset), base (symbol) or section ("SEG,sect")')
    elif anchors == ['at']:
        try:
            offset(e['at'])
        except ValueError as x:
            bad.append(str(x))
        if count != 1:
            bad.append('at names one place; count must be 1')
    elif anchors == ['base']:
        if not (isinstance(e['base'], str) and e['base']):
            bad.append('base must be a symbol name')
        limit = e.get('limit')
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < len(find):
            bad.append('base needs limit: how many bytes after the symbol to search (at least the find length)')
    else:
        if not (isinstance(e['section'], str) and re.fullmatch(r'[^,]{1,16},[^,]{1,16}', e['section'])):
            bad.append('section must be "SEGMENT,section"')
    if 'limit' in e and 'base' not in e:
        bad.append('limit only goes with base')
    return bad


def _matcher_problems(e):
    bad = []
    if 'fn' not in e and 'resolved' not in e:
        bad.append('a matcher needs fn (path.py:function) and/or resolved sites')
    if 'fn' in e and not (isinstance(e['fn'], str) and FN.match(e['fn']) and _relative_path(e['fn'].split(':')[0])):
        bad.append('fn must be "relative/path.py:function"')
    resolved = e.get('resolved', {})
    if not isinstance(resolved, dict):
        return bad + ['resolved maps an input sha256 to a list of sites']
    for sha, sites in resolved.items():
        if not SHA.match(sha):
            bad.append('resolved key %r is not a lower-case sha256' % sha)
        if not isinstance(sites, list) or not sites:
            bad.append('resolved %s: needs a non-empty list of sites' % sha[:12])
            continue
        for s in sites:
            try:
                if not isinstance(s, dict) or set(s) - {'at', 'before', 'after', 'reason'}:
                    raise ValueError('a site is {"at", "before", "after", "reason"}')
                offset(s['at'])
                if len(hexbytes(s['before'])) != len(hexbytes(s['after'])):
                    raise ValueError('before and after must be the same length')
            except (ValueError, KeyError) as x:
                bad.append('resolved %s: %s' % (sha[:12], x))
    return bad


def _op_problems(e):
    op, args = e.get('op'), e.get('args', {})
    if op in EXTERNAL_OPS:
        return [] if isinstance(args, dict) else ['args must be an object']
    if op not in OPS:
        return ['op must be one of %s' % ', '.join(sorted(set(OPS) | set(EXTERNAL_OPS)))]
    types, required = OPS[op]
    if not isinstance(args, dict):
        return ['args must be an object']
    bad = []
    missing = required - set(args)
    if missing:
        bad.append('%s needs args %s' % (op, ', '.join(sorted(missing))))
    for k, v in args.items():
        if k not in types:
            bad.append('%s: unknown arg %s' % (op, k))
        elif types[k] is not object and (not isinstance(v, types[k]) or (types[k] is int and isinstance(v, bool))):
            bad.append('%s: %s must be %s' % (op, k, types[k].__name__))
    if op == 'macho.add-dylib' and isinstance(args.get('path'), str) and not args['path'].startswith('/'):
        bad.append('macho.add-dylib: path must be absolute')
    if op == 'plist.set' and isinstance(args.get('key'), list) and (
            not args['key'] or not all(isinstance(k, (str, int)) and not isinstance(k, bool) for k in args['key'])):
        bad.append('plist.set: key is a non-empty list of dictionary keys and array indexes')
    return bad


def origin_text(origin):
    text = {'apple': 'Apple', 'tahoe': 'Tahoe donor', 'oclp': 'OCLP', 'hao': 'hao'}.get(origin.get('kind'), '?')
    if origin.get('name'):
        text += ' · ' + origin['name']
    if origin.get('row'):
        text += ' (row %s)' % origin['row']
    return text


def where_text(e):
    if 'at' in e:
        return 'at %#x' % offset(e['at'])
    if 'base' in e:
        return 'near %s (within %d bytes)' % (e['base'], e['limit'])
    return 'in %s' % e['section']


def edit_summary(e):
    """One line: what kind of change and where."""
    kind = e['kind']
    if kind == 'literal':
        n = e.get('count', 1)
        return 'literal %s, %d byte%s%s' % (where_text(e), len(hexbytes(e['find'])),
                                          '' if len(hexbytes(e['find'])) == 1 else 's',
                                          '' if n == 1 else ', %d places' % n)
    if kind == 'matcher':
        n = len(e.get('resolved', {}))
        return 'matcher %s%s' % (e.get('fn', '(resolved sites only)'),
                                 ', resolved for %d input%s' % (n, '' if n == 1 else 's') if n else '')
    op, args = e['op'], e.get('args', {})
    if op == 'macho.add-dylib':
        return '%s %s %s' % (op, 'LC_LOAD_WEAK_DYLIB' if args.get('weak') else 'LC_LOAD_DYLIB', args['path'])
    if op == 'plist.set':
        return '%s %s = %s' % (op, ':'.join(str(k) for k in args['key']), json.dumps(args['value']))
    if op in EXTERNAL_OPS:
        return '%s (applied by %s)' % (op, EXTERNAL_OPS[op])
    return op


def describe(spec):
    """Plain-text explanation of SPEC: the file, where it comes from, and each edit in order with its reason."""
    lines = [spec['path'],
             '  origin   %s' % origin_text(spec['origin']),
             '  applied  by the %s' % spec.get('applied_by', 'installer')]
    for n, e in enumerate(spec['edits'], 1):
        lines.append('  %d. %s  %s' % (n, e['id'], edit_summary(e)))
        if e['kind'] == 'literal':
            lines.append('       bytes  %s  ->  %s' % (spaced(hexbytes(e['find'])), spaced(hexbytes(e['replace']))))
        if e.get('asm'):
            lines.append('       asm    %s  ->  %s' % (e['asm']['before'], e['asm']['after']))
        lines.append('       why    %s' % e['reason'])
        if e.get('evidence'):
            lines.append('       proof  %s' % e['evidence'])
    lines.append('  sign     %s' % sign_text(spec.get('sign')))
    return lines


def sign_text(sign):
    if not sign:
        return 'not signed'
    out = 'ad hoc, once'
    if sign.get('identifier'):
        out += ', identifier %s' % sign['identifier']
    if sign.get('preserve'):
        out += ', keeping the file\'s %s' % ', '.join(sign['preserve'])
    return out


def codesign_args(sign, existing_identifier=None):
    """codesign arguments for SIGN; EXISTING_IDENTIFIER is the input file's, when SIGN preserves it."""
    args = ['codesign', '-f', '-s', '-']
    preserve = [p for p in sign.get('preserve', []) if p != 'identifier']
    if sign.get('identifier'):
        args += ['-i', sign['identifier']]
    elif 'identifier' in sign.get('preserve', []):
        if not existing_identifier:
            raise ValueError('sign preserves the identifier, but the input file has none')
        args += ['-i', existing_identifier]
    if preserve:
        args.append('--preserve-metadata=' + ','.join(preserve))
    return args
