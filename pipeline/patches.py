#!/usr/bin/python3 -I
"""patches.py: which patches of a set apply to a build, and at which stage.

  patches.py plan SET TARGET BUILD [--locks DIR:DIR...] [--skip-optional GROUP]... [--record FILE]
                                     one line per patch that applies, in set order: STAGE NAME [INPUT ...]
                                     (SET = pipeline/sets/SET.txt, TARGET = pipeline/targets/MODEL.json). Rows of
                                     optional patches are looked up in --locks (default pipeline/inputs/BUILD then
                                     pipeline/inputs/common); --record writes which optional patches ran or were skipped
  patches.py revisions NAME ...      NAME rN per patch, for the build record (MANIFEST.txt)
  patches.py tracker [OUT]           the patch tracker (patches/TRACKER.md): every patch, revision, sets, validation

  patches.py check                   every patch against the patch format below; one line per problem, exit 1 if any

patches/NAME/patch.json: {"revision": N, "stage": "oclp-hook" | "post" | "recovery", "applies": "any" | {"models":
[...], "builds": [...]}, "inputs": [inputs-lock row names], "status": STATUS, "validated": true | false}. revision counts
up with every change to the patch or its input rows (a number tells which is newer; a hash does not), and the README's
"Revisions" section names the current one. status says what the patch is for: product (meant for the shipped image),
dev (test access and instrumentation), diagnostic (capture for an investigation), experiment, unfinished, deprecated.
validated is false until a boot test proves the current revision; the README's "## Validation" then starts with NEEDS
VALIDATION. The README has "## Verify", "## Validation" and, last, "## Revisions" (newest line = revision).
pipeline/run takes the oclp-hook and post patches, pipeline/recovery the recovery ones. A key left out of applies
matches every value. A patch that does not apply is skipped with a note on stderr; a missing or unknown stage or applies
key, or a missing revision, stops.

Optional patches ("optional": GROUP in patch.json) need inputs most builders do not have (e.g. caches harvested from a
running laptop). plan skips one, with a note on stderr, when one of its rows is not in the locks or its path is not
there, or when its GROUP is passed to --skip-optional. A row that is there but fails its sha256 stops: a corrupted
input is never skipped. "needs_any": GROUP skips a patch unless a patch of GROUP without needs_any runs (a consumer
with nothing to consume). Rows of patches that are not optional are not looked at here; staging stops on a missing one.
"""
import json, os, re, sys

def directory(name, root):
    return os.path.join(root, name)


def patch_names(root):
    return sorted(n for n in os.listdir(root) if os.path.isdir(os.path.join(root, n)))

TOP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STAGES = ('oclp-hook', 'post', 'recovery')
STATUSES = ('product', 'dev', 'diagnostic', 'experiment', 'unfinished', 'deprecated')
SHIPPING_SETS = ('recovery',)   # sets whose output goes to people: product patches only
APPLIES = ('models', 'builds')


def names(setfile):
    out = []
    for line in open(setfile):
        line = line.split('#', 1)[0].strip()
        if line:
            out.append(line)
    return out


def applies(rule, model, build):
    if rule == 'any':
        return True
    if not isinstance(rule, dict) or not rule or set(rule) - set(APPLIES):
        raise ValueError('applies must be "any" or an object with %s, not %r' % (' and/or '.join(APPLIES), rule))
    return model in rule.get('models', [model]) and build in rule.get('builds', [build])


def revision(name, root=os.path.join(TOP, 'patches')):
    r = json.load(open(os.path.join(directory(name, root), 'patch.json'))).get('revision')
    if not isinstance(r, int) or r < 1:
        raise ValueError('%s: patch.json has no "revision": N (and README "Revisions" line)' % name)
    return r


def default_locks(build):
    return [os.path.join(TOP, 'pipeline', 'inputs', build), os.path.join(TOP, 'pipeline', 'inputs', 'common')]


def missing_row(row, locks):
    """Why ROW cannot be used (None: it can): no row file in LOCKS, or its path is not there. A row whose file is there
    but is not its sha256 raises: an optional patch is skipped for a missing input, never for a corrupted one."""
    import runpy   # lock.py is not importable under python -I (no script folder on sys.path)
    lock = runpy.run_path(os.path.join(TOP, 'pipeline', 'lock.py'))
    for d in locks:
        if os.path.isfile(os.path.join(d, row)):
            sha, p = lock['rows'](d)[row]
            if not os.path.exists(p):
                return 'row %s: %s is not there' % (row, p)
            if sha != '-' and (not os.path.isfile(p) or lock['sha256'](p) != sha):
                raise ValueError('row %s: %s is not %s (a corrupted input stops, it is not skipped)' % (row, p, sha))
            return None
    return 'no row %s in %s' % (row, ', '.join(os.path.relpath(d, TOP) if d.startswith(TOP) else d for d in locks))


def plan(setfile, target, build, root=os.path.join(TOP, 'patches'), locks=None, skip=(), record=None):
    """[(stage, name, inputs)] in set order. LOCKS (row folders, searched in order) and SKIP (optional groups to leave
    out) decide which optional patches run; with LOCKS None their rows are not looked at. RECORD, a dict, receives
    {"ran": {name: group}, "skipped": {name: {"group", "reason"}}} for the optional patches that apply."""
    model = json.load(open(target))['model']
    out, optional = [], {}
    if record is None:
        record = {}
    record.update(ran={}, skipped={})
    for name in names(setfile):
        p = json.load(open(os.path.join(directory(name, root), 'patch.json')))
        revision(name, root)
        if p.get('stage') not in STAGES:
            raise ValueError('%s: stage %r is not one of %s' % (name, p.get('stage'), ', '.join(STAGES)))
        try:
            ok = applies(p.get('applies'), model, build)
        except ValueError as e:
            raise ValueError('%s: %s' % (name, e))
        if not ok:
            print('patches: %s does not apply to %s %s; skipped' % (name, model, build), file=sys.stderr)
            continue
        group = p.get('optional')
        if group:
            reason = 'group %s skipped (--skip-optional)' % group if group in skip else None
            for row in p.get('inputs', []) if locks is not None and not reason else []:
                reason = missing_row(row, locks)
                if reason:
                    break
            if reason:
                record['skipped'][name] = dict(group=group, reason=reason)
                print('patches: optional %s skipped: %s' % (name, reason), file=sys.stderr)
                continue
            optional[name] = (group, p.get('needs_any'))
        out.append((p['stage'], name, list(p.get('inputs', []))))
    for name, (group, needs) in optional.items():
        if needs and not any(g == needs and not nn and n not in record['skipped'] for n, (g, nn) in optional.items()):
            reason = 'no provider of optional group %s runs' % needs
            record['skipped'][name] = dict(group=group, reason=reason)
            print('patches: optional %s skipped: %s' % (name, reason), file=sys.stderr)
    out = [o for o in out if o[1] not in record['skipped']]
    record['ran'] = {n: g for n, (g, _) in optional.items() if n not in record['skipped']}
    for g in skip:
        if g not in {g for g, _ in optional.values()} | {v['group'] for v in record['skipped'].values()}:
            print('patches: no patch of optional group %s in %s' % (g, os.path.basename(str(setfile))), file=sys.stderr)
    return out


TRACKED_SETS = ('golden-gate', 'recovery', 'stock')


def tracker(root=os.path.join(TOP, 'patches')):
    """Markdown table of every patch: revision, stage, status, which shipped sets select it, validation (patch.json
    "validated"), newest Revisions line. Generated; never edited by hand."""
    sets = {s: set(names(os.path.join(TOP, 'pipeline', 'sets', s + '.txt'))) for s in TRACKED_SETS}
    rows = ['# Patch tracker', '',
            'Generated by `python3 -I pipeline/patches.py tracker` from each patch.json and README; the pipeline tests',
            'fail when it is stale. Validation: **needs** = patch.json "validated": false (the README says NEEDS',
            'VALIDATION), ok = a boot test proved the current revision.', '',
            'Optional: the patch\'s optional group (skipped when its inputs are missing or the group is skipped).', '',
            '| Patch | Rev | Stage | Status | Optional | ' + ' | '.join(TRACKED_SETS) + ' | Validation | Newest revision |',
            '|---|---|---|---|---|' + '---|' * len(TRACKED_SETS) + '---|---|']
    for name in patch_names(root):
        pj = os.path.join(directory(name, root), 'patch.json')
        if not os.path.isfile(pj):
            continue
        p = json.load(open(pj))
        text = open(os.path.join(directory(name, root), 'README.md')).read()
        newest = text.split('\n## Revisions\n', 1)[1] if '\n## Revisions\n' in text else ''
        newest = next((l[2:] for l in newest.splitlines() if l.startswith('- r')), '').replace('|', '/')
        rows.append('| %s | r%s | %s | %s | %s | %s | %s | %s |' % (name, p.get('revision', '?'), p.get('stage', '?'),
                    p.get('status', '?'), p.get('optional', ''), ' | '.join('x' if name in sets[s] else '' for s in TRACKED_SETS),
                    'ok' if p.get('validated') else '**needs**', newest[:110]))
    return '\n'.join(rows) + '\n'


def check(root=os.path.join(TOP, 'patches'), sets_dir=os.path.join(TOP, 'pipeline', 'sets')):
    """Problems with the patch format, one string each (empty: every patch follows it)."""
    bad = []
    shipping = {s: set(names(os.path.join(sets_dir, s + '.txt'))) for s in SHIPPING_SETS
                if os.path.isfile(os.path.join(sets_dir, s + '.txt'))}
    for name in patch_names(root):
        d = directory(name, root)
        if not os.path.isdir(d):
            continue
        if not os.path.isfile(os.path.join(d, 'patch.json')):
            bad.append('%s: no patch.json' % name)
            continue
        p = json.load(open(os.path.join(d, 'patch.json')))
        if not isinstance(p.get('revision'), int) or p['revision'] < 1:
            bad.append('%s: revision must be a number >= 1' % name)
        if p.get('stage') not in STAGES:
            bad.append('%s: stage %r is not one of %s' % (name, p.get('stage'), ', '.join(STAGES)))
        if p.get('status') not in STATUSES:
            bad.append('%s: status %r is not one of %s' % (name, p.get('status'), ', '.join(STATUSES)))
        if not isinstance(p.get('validated'), bool):
            bad.append('%s: validated must be true or false' % name)
        for key in ('optional', 'needs_any'):
            if key in p and not (isinstance(p[key], str) and re.fullmatch(r'[a-z0-9][a-z0-9-]*', p[key])):
                bad.append('%s: %s must be a group name (lower-case letters, digits, -)' % (name, key))
        if 'needs_any' in p and 'optional' not in p:
            bad.append('%s: needs_any without optional (a patch that can be skipped must be optional)' % name)
        unknown = set(p) - {'revision', 'stage', 'applies', 'inputs', 'status', 'validated', 'optional', 'needs_any'}
        if unknown:
            bad.append('%s: unknown patch.json keys %s' % (name, ', '.join(sorted(unknown))))
        text = open(os.path.join(d, 'README.md')).read() if os.path.isfile(os.path.join(d, 'README.md')) else ''
        heads = re.findall(r'^## (.+?)\s*$', text, re.M)
        for h in ('Verify', 'Validation', 'Revisions'):
            if h not in heads:
                bad.append('%s: README has no "## %s"' % (name, h))
        if heads and heads[-1] != 'Revisions':
            bad.append('%s: "## Revisions" must be the README\'s last section' % name)
        newest = re.search(r'^## Revisions\n+- r(\d+) ', text, re.M)
        if newest and int(newest.group(1)) != p.get('revision'):
            bad.append('%s: newest Revisions line is r%s, patch.json says r%s' % (name, newest.group(1), p.get('revision')))
        val = text.split('\n## Validation\n', 1)[1].lstrip() if '\n## Validation\n' in text else ''
        if val and val.startswith('NEEDS VALIDATION') == bool(p.get('validated')):
            bad.append('%s: patch.json validated=%s but the README Validation %s with NEEDS VALIDATION'
                       % (name, p.get('validated'), 'starts' if p.get('validated') else 'does not start'))
        for s, members in shipping.items():
            if name in members and p.get('status') != 'product':
                bad.append('%s: status %s, but set %s ships to people (product patches only)' % (name, p.get('status'), s))
    return bad

if __name__ == '__main__':
    if sys.argv[1:2] == ['path'] and len(sys.argv) == 3:
        print(directory(sys.argv[2], os.path.join(TOP, 'patches')))
        sys.exit(0)
    if sys.argv[1:2] == ['tracker']:
        out = sys.argv[2] if len(sys.argv) > 2 else os.path.join(TOP, 'patches', 'TRACKER.md')
        open(out, 'w').write(tracker())
        sys.exit(0)
    if sys.argv[1:2] == ['check']:
        problems = check()
        print('\n'.join(problems) or 'patches: every patch follows the format')
        sys.exit(1 if problems else 0)
    if sys.argv[1:2] == ['revisions']:
        try:
            print(' '.join('%s r%d' % (n, revision(n)) for n in sys.argv[2:]))
        except (ValueError, OSError) as e:
            sys.exit('patches: %s' % e)
        sys.exit(0)
    args, locks, skip, out, rest = [], None, [], None, iter(sys.argv[2:])
    for a in rest:
        if a not in ('--locks', '--skip-optional', '--record'):
            args.append(a)
            continue
        value = next(rest, None)
        if value is None:
            sys.exit(__doc__)
        if a == '--locks':
            locks = [d for d in value.split(':') if d]
        elif a == '--skip-optional':
            skip.append(value)
        else:
            out = value
    if sys.argv[1:2] != ['plan'] or len(args) != 3 or any(a.startswith('--') for a in args):
        sys.exit(__doc__)
    try:
        record = {}
        for stage, name, inputs in plan(*args, locks=locks if locks is not None else default_locks(args[2]),
                                        skip=skip, record=record):
            print(' '.join([stage, name] + inputs))
        if out:
            open(out, 'w').write(json.dumps(dict(record, skip_optional=skip), indent=1, sort_keys=True) + '\n')
    except (ValueError, OSError, KeyError) as e:
        sys.exit('patches.py: %s' % e)
