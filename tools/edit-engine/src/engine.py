"""Apply one edit spec to one file's bytes, in order, with checks before and after every write.

apply() never touches the filesystem except to load a matcher function. It refuses an edit whose bytes are not as
declared, two edits that touch the same bytes (or the same plist key), and a result that no longer parses as the
x86_64 Mach-O the spec declared. A known input whose output differs from "expect" is reported, not refused: every
check passed, it is just not the combination recorded before.

Events: on_event receives one dict per step (ev = file.start, edit.apply, edit.fail, file.end), the same data the
command line prints at each detail level and writes with --log json.
"""
import hashlib
import os
import plistlib
import runpy

import macho
import spec as S
import store as ST


class EditError(ValueError):
    def __init__(self, edit_id, message):
        super().__init__('%s: %s' % (edit_id, message) if edit_id else message)
        self.edit_id = edit_id


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def search(data, lo, hi, pattern, mask):
    """Every offset in [LO, HI) where PATTERN matches DATA under MASK (bits set in MASK must match)."""
    n, hi = len(pattern), min(hi, len(data))
    best, i = (0, 0), 0
    while i < n:                      # the longest fully-compared run is found with bytes.find
        if mask[i] == 0xff:
            j = i
            while j < n and mask[j] == 0xff:
                j += 1
            if j - i > best[1] - best[0]:
                best = (i, j)
            i = j
        else:
            i += 1
    a, b = best

    def full(start):
        return all(data[start + k] & mask[k] == pattern[k] & mask[k] for k in range(n))

    hits = []
    if b > a:
        key, stop = pattern[a:b], hi - (n - b)
        pos = data.find(key, lo + a, stop)
        while pos != -1:
            if full(pos - a):
                hits.append(pos - a)
            pos = data.find(key, pos + 1, stop)
    else:
        hits = [p for p in range(lo, hi - n + 1) if full(p)]
    return hits


def _literal(e, data):
    find, replace = S.hexbytes(e['find']), S.hexbytes(e['replace'])
    mask = S.hexbytes(e['mask']) if 'mask' in e else b'\xff' * len(find)
    rmask = S.hexbytes(e['replace_mask']) if 'replace_mask' in e else b'\xff' * len(replace)
    count = e.get('count', 1)
    if 'at' in e:
        lo = S.offset(e['at'])
        hi, label = lo + len(find), None
    elif 'base' in e:
        lo = macho.Image(data).symbol_offset(e['base'])
        hi, label = lo + e['limit'], e['base']
    else:
        seg, sect = e['section'].split(',')
        lo, size = macho.Image(data).section(seg, sect)
        hi, label = lo + size, e['section']
    hits = search(data, lo, hi, find, mask)
    if len(hits) != count:
        hint = ''
        if not hits and len(search(data, lo, hi, replace, rmask)) == count:
            hint = ' (the replacement is already there: this file was edited before)'
        raise ValueError('found %d of %d %s%s' % (len(hits), count, S.where_text(e), hint))
    out, sites = bytearray(data), []
    for at in hits:
        before = bytes(out[at:at + len(find)])
        after = bytes((b & ~m & 0xff) | (r & m) for b, r, m in zip(before, replace, rmask))
        out[at:at + len(after)] = after
        sites.append((at, before, after))
    where = ['%#x' % at + (' (%s +%#x)' % (label, at - lo) if label else '') for at in hits]
    return bytes(out), sites, {'found': len(hits), 'count': count, 'where': where}


def _matcher(e, data, input_sha, repo):
    resolved = e.get('resolved', {}).get(input_sha)
    if resolved is not None:
        sites = [(S.offset(s['at']), S.hexbytes(s['before']), S.hexbytes(s['after'])) for s in resolved]
        by = 'resolved'
    elif 'fn' in e:
        path, fn = e['fn'].rsplit(':', 1)
        raw = runpy.run_path(os.path.join(repo, path))[fn](data)
        sites = [(int(s[0]), bytes(s[1]), bytes(s[2])) for s in raw]
        by = 'matcher'
        if not sites:
            raise ValueError('the matcher found no sites')
    else:
        raise ValueError('no resolved sites for this input (sha256 %s) and no matcher function' % input_sha)
    out = bytearray(data)
    for at, before, after in sites:
        if len(before) != len(after):
            raise ValueError('site %#x: before and after differ in length' % at)
        if bytes(out[at:at + len(before)]) != before:
            raise ValueError('bytes at %#x are %s, expected %s' % (at, S.spaced(out[at:at + len(before)]),
                                                                  S.spaced(before)))
        out[at:at + len(after)] = after
    return bytes(out), sites, {'found': len(sites), 'resolved_by': by, 'where': ['%#x' % s[0] for s in sites]}


def _plist_set(data, key, value):
    fmt = plistlib.FMT_BINARY if data.startswith(b'bplist00') else plistlib.FMT_XML
    root = plistlib.loads(data)
    node = root
    for k in key[:-1]:
        node = node[k]
    last = key[-1]
    if isinstance(node, list) and not (isinstance(last, int) and -len(node) <= last < len(node)):
        raise ValueError('index %r is outside the array' % (last,))
    if isinstance(node, dict) and not isinstance(last, str):
        raise ValueError('key %r is not a dictionary key' % (last,))
    old = node[last] if (isinstance(node, dict) and last in node) or isinstance(node, list) else None
    node[last] = value
    return plistlib.dumps(root, fmt=fmt, sort_keys=False), old


def _op(e, data):
    op, args = e['op'], e.get('args', {})
    if op in S.EXTERNAL_OPS:
        raise ValueError('%s is applied by %s, not by the edit engine' % (op, S.EXTERNAL_OPS[op]))
    if op == 'macho.add-dylib':
        out, (a, b), info = macho.add_dylib(
            data, args['path'], weak=args.get('weak', False), timestamp=args.get('timestamp', 2),
            current_version=args.get('current_version', 0), compat_version=args.get('compat_version', 0))
        return out, [(a, data[a:b], out[a:b])], dict(info, found=1, where=['%#x' % a])
    if op == 'plist.set':
        try:
            out, old = _plist_set(data, args['key'], args['value'])
        except (KeyError, IndexError, TypeError) as x:
            raise ValueError('no such key path %s (%s)' % (args['key'], x))
        return out, [], {'found': 1, 'key': args['key'], 'old': old if isinstance(old, (str, int, bool, float)) else None}
    raise ValueError('unknown op %s' % op)


def _claims(e, sites):
    if e['kind'] == 'op' and e['op'] == 'plist.set':
        return [('key', tuple(e['args']['key']))]
    return [('bytes', at, at + len(after)) for at, _, after in sites]


def _conflict(a, b):
    if a[0] != b[0]:
        return False
    if a[0] == 'bytes':
        return a[1] < b[2] and b[1] < a[2]
    short = min(len(a[1]), len(b[1]))
    return a[1][:short] == b[1][:short]


def apply(spec, data, *, repo=None, on_event=None):
    """(output bytes, record). Raises EditError naming the edit that could not be applied."""
    problems = S.validate(spec)
    if problems:
        raise EditError(None, 'invalid spec: ' + '; '.join(problems))
    emit = on_event or (lambda event: None)
    repo = repo or os.getcwd()
    data = bytes(data)
    input_sha = sha256(data)
    emit({'ev': 'file.start', 'path': spec['path'], 'origin': spec['origin'], 'input_sha256': input_sha,
          'size': len(data)})
    claimed, kept, edits = [], [], []
    total = len(spec['edits'])
    for n, e in enumerate(spec['edits'], 1):
        try:
            if e['kind'] == 'literal':
                data, sites, info = _literal(e, data)
            elif e['kind'] == 'matcher':
                data, sites, info = _matcher(e, data, input_sha, repo)
            else:
                data, sites, info = _op(e, data)
            for claim in _claims(e, sites):
                for other, other_id in claimed:
                    if _conflict(claim, other):
                        raise ValueError('touches the same %s as %s' % ('bytes' if claim[0] == 'bytes' else 'key',
                                                                        other_id))
                claimed.append((claim, e['id']))
        except (ValueError, IndexError) as x:
            emit({'ev': 'edit.fail', 'id': e['id'], 'n': n, 'of': total, 'kind': e['kind'], 'error': str(x)})
            raise EditError(e['id'], str(x)) from None
        kept += [(at, after, e['id']) for at, _, after in sites]
        row = {'id': e['id'], 'n': n, 'of': total, 'kind': e['kind'], 'op': e.get('op'), 'summary': S.edit_summary(e),
               'sites': [{'at': '%#x' % at, 'before': before.hex(), 'after': after.hex()} for at, before, after in sites],
               **info}
        edits.append(row)
        emit(dict(row, ev='edit.apply', reason=e['reason'], asm=e.get('asm')))
    for at, after, eid in kept:   # postconditions: every written byte is still what its edit wrote
        if data[at:at + len(after)] != after:
            raise EditError(eid, 'postcondition: bytes at %#x were changed by a later edit' % at)
    if spec.get('arch') == 'x86_64':
        try:
            macho.Image(data)
        except macho.MachOError as x:
            raise EditError(None, 'postcondition: the result is not a valid x86_64 Mach-O (%s)' % x)
    output_sha = sha256(data)
    expected = spec.get('expect', {}).get(input_sha)
    status = 'unknown-input' if expected is None else 'match' if expected == output_sha else 'differs'
    record = {'path': spec['path'], 'origin': spec['origin'], 'input_sha256': input_sha, 'output_sha256': output_sha,
              'expected': status, 'edits': edits, 'sign': spec.get('sign')}
    emit({'ev': 'file.end', 'path': spec['path'], 'output_sha256': output_sha, 'expected': status,
          'sign': spec.get('sign')})
    return data, record


def apply_stored(spec, data, store, *, repo=None, on_event=None):
    """apply() through an artifact store: reuse the stored result when its key matches and its output object
    still hashes to the recorded sha256; otherwise apply and store the original, the result and the record."""
    problems = S.validate(spec)
    if problems:
        raise EditError(None, 'invalid spec: ' + '; '.join(problems))
    emit = on_event or (lambda event: None)
    repo = repo or os.getcwd()
    data = bytes(data)
    k = ST.key(spec, sha256(data), repo)
    hit = store.lookup(k)
    if hit is not None:
        out, record = hit
        emit({'ev': 'file.start', 'path': spec['path'], 'origin': spec['origin'], 'input_sha256': record['input_sha256'],
              'size': len(data)})
        emit({'ev': 'file.reused', 'path': spec['path'], 'key': k, 'output_sha256': record['output_sha256'],
              'edits': len(record['edits'])})
        return out, dict(record, key=k, reused=True)
    out, record = apply(spec, data, repo=repo, on_event=on_event)
    store.save(k, spec, data, out, record, S.describe(spec))
    return out, dict(record, key=k, reused=False)
