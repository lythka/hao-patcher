#!/usr/bin/python3 -I
"""patches.py: which patches of a set apply to a build, and at which stage.

  patches.py plan SET TARGET BUILD [--locks DIR:DIR...] [--skip-optional GROUP]... [--record FILE] [--packs auto|A,B]
                                     one line per patch that applies, in set order: STAGE NAME [INPUT ...]
                                     (SET = pipeline/sets/SET.txt, TARGET = pipeline/targets/MODEL.json). Rows of
                                     optional patches are looked up in --locks (default pipeline/inputs/BUILD then
                                     pipeline/inputs/common); --record writes which optional patches ran or were skipped
                                     --packs: a recipe listed in a pack (packs/NAME.json) runs only when its pack is
                                     selected; auto detects them from the target (OCLP patch sets, board id)
  patches.py packs TARGET            the packs detected for TARGET, with their phase and recipes
  patches.py revisions NAME ...      NAME rN per patch, for the build record (MANIFEST.txt)
  patches.py tracker [OUT]           the patch tracker (patches/TRACKER.md): every patch, revision, sets, validation

  patches.py check                   every patch against the patch format below; one line per problem, exit 1 if any
  patches.py digest NAME             sha256 of everything that decides what NAME installs (for validated_digest)
  patches.py explain [NAME | PATH]   what each declared file comes from and every edit to it, with reasons (one patch,
                                     or every file whose path is or ends with /PATH); no argument: every patch
  patches.py catalog [OUT]           the patch catalog (docs/PATCHES.md): explain for every patch, as Markdown

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

A revision that only changes the declaration (README, patch.json "files", edits/, tests/) keeps a boot test's proof:
"validated_revision": N names the revision the test proved and "validated_digest" the `patches.py digest` taken
then; check accepts validated true for a later revision only while the digest is unchanged (hooks, src/, apply.sh,
make, build/, kexts.txt and the input rows' files are what it covers).

"files" (optional) declares every file the patch adds or changes on the installed system, for patches.py explain, the
catalog and (later) the ledger: [{"path": RELATIVE, "edits": "edits/FILE.json"} | {"path": RELATIVE, "origin": {"kind":
apple|tahoe|oclp|hao, "row": INPUT_ROW, "name": ..., "note": ...}}], optionally "tree": true (an origin entry
for a folder: every file under it), "install": {"mode", "owner", "flags"} and "source" (where the bytes are inside
the origin row: a relative path, or "ARCHIVE!MEMBER" for a file in a tar; pipeline/materialize.py needs it when the row
is a folder laid out differently from the install path); an edits entry installed under another name than the file it
starts from names that file as "input": {"kind", "path"}. An edits file is an edit spec for
tools/edit-engine (format in its README) and must name the same path; every patches/NAME/edits/*.json must be declared.
"""
import hashlib, json, os, re, sys

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


PACKS = os.path.join(TOP, 'packs')
PHASES = ('installer', 'live-capable')


def load_packs(packs_dir=PACKS):
    out = {}
    for n in sorted(os.listdir(packs_dir)) if os.path.isdir(packs_dir) else []:
        if n.endswith('.json'):
            with open(os.path.join(packs_dir, n)) as f:
                out[n[:-5]] = json.load(f)
    return out


def pack_problems(packs, root=os.path.join(TOP, 'patches')):
    """Problems with packs/*.json: keys, phase, detect rule, and each recipe in at most one pack."""
    bad, member = [], {}
    known = set(patch_names(root))
    for name, p in packs.items():
        where = 'packs/%s.json' % name
        unknown = set(p) - {'format', 'description', 'detect', 'oclp_patchsets', 'phase', 'proven_on', 'recipes'}
        if unknown:
            bad.append('%s: unknown keys %s' % (where, ', '.join(sorted(unknown))))
        if p.get('format') != 1 or p.get('phase') not in PHASES:
            bad.append('%s: needs format 1 and phase %s' % (where, ' or '.join(PHASES)))
        d = p.get('detect')
        if not (isinstance(d, dict) and len(d) == 1 and (
                d.get('any_intel') is True or (isinstance(d.get('oclp_patchsets'), list) and d['oclp_patchsets'])
                or (isinstance(d.get('board_ids'), list) and d['board_ids']))):
            bad.append('%s: detect is one of {"any_intel": true}, {"oclp_patchsets": [...]}, {"board_ids": [...]}' % where)
        for r in p.get('recipes', []):
            if r not in known:
                bad.append('%s: no recipe %s' % (where, r))
            elif r in member:
                bad.append('%s: %s is already in pack %s' % (where, r, member[r]))
            member[r] = name
    return bad


def detect_packs(target, packs=None):
    """Names of the packs TARGET (a targets/*.json path) selects, from its OCLP profile's patch sets and board id."""
    packs = load_packs() if packs is None else packs
    with open(target) as f:
        t = json.load(f)
    profile = os.path.join(TOP, 'tools', 'oclp-hardware', 'profiles',
                           os.path.splitext(os.path.basename(target))[0] + '.json')
    patchsets = set()
    if os.path.isfile(profile):
        with open(profile) as f:
            patchsets = set(json.load(f).get('patchset_order', []))
    out = []
    for name, p in packs.items():
        d = p.get('detect', {})
        if d.get('any_intel') or patchsets & set(d.get('oclp_patchsets', [])) or t.get('board_id') in d.get('board_ids', []):
            out.append(name)
    return out


def plan(setfile, target, build, root=os.path.join(TOP, 'patches'), locks=None, skip=(), record=None, packs=None,
         pack_defs=None):
    """PACKS (names of selected packs, or None for no pack filtering): a recipe listed in a pack that is not selected
    is skipped with a note."""
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
        if packs is not None:
            owner = next((pn for pn, pd in (pack_defs if pack_defs is not None else load_packs()).items()
                          if name in pd.get('recipes', [])), None)
            if owner is not None and owner not in packs:
                print('patches: %s is in pack %s, not selected; skipped' % (name, owner), file=sys.stderr)
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


TRACKED_SETS = ('golden-gate-26B131', 'golden-gate', 'recovery', 'stock')


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
                    ('ok (r%s)' % p['validated_revision'] if 'validated_revision' in p else 'ok')
                    if p.get('validated') else '**needs**', newest[:110]))
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
        unknown = set(p) - {'revision', 'stage', 'applies', 'inputs', 'status', 'validated', 'optional', 'needs_any',
                            'files', 'validated_revision', 'validated_digest'}
        if ('validated_revision' in p) != ('validated_digest' in p):
            bad.append('%s: validated_revision and validated_digest go together' % name)
        elif 'validated_revision' in p:
            vr = p['validated_revision']
            if not p.get('validated'):
                bad.append('%s: validated_revision without validated true' % name)
            elif not isinstance(vr, int) or not 1 <= vr < (p.get('revision') or 0):
                bad.append('%s: validated_revision must be an earlier revision (drop it when the current one is proven)'
                           % name)
            elif p['validated_digest'] != recipe_digest(name, root):
                bad.append('%s: validated at r%s, but what it installs changed since (digest differs): set validated '
                           'false and NEEDS VALIDATION, or validate again' % (name, vr))
        if unknown:
            bad.append('%s: unknown patch.json keys %s' % (name, ', '.join(sorted(unknown))))
        bad += ['%s: %s' % (name, problem) for problem in file_problems(d, p)]
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
    if os.path.abspath(str(root)) == os.path.join(TOP, 'patches'):
        bad += pack_problems(load_packs(), root)
    return bad

DIGEST_SKIP = {'README.md', 'patch.json', 'edits', 'tests'}


def recipe_digest(name, root=os.path.join(TOP, 'patches'), inputs=os.path.join(TOP, 'pipeline', 'inputs')):
    """sha256 over everything that decides what NAME installs: its files except DIGEST_SKIP (and caches and
    patches/*/build/build output), and every input row file of its inputs under INPUTS."""
    d = directory(name, root)
    h = hashlib.sha256()
    for dirpath, dirs, files in os.walk(d):
        rel = os.path.relpath(dirpath, d)
        dirs[:] = sorted(x for x in dirs if x != '__pycache__' and not (rel == '.' and x in DIGEST_SKIP)
                         and not (rel == 'build' and x == 'build'))
        for f in sorted(files):
            if (rel == '.' and f in DIGEST_SKIP) or f.endswith('.pyc') or f == '.DS_Store':
                continue
            with open(os.path.join(dirpath, f), 'rb') as fh:
                h.update(b'file\0%s\0%s\n' % (os.path.normpath(os.path.join(rel, f)).encode(),
                                                hashlib.sha256(fh.read()).hexdigest().encode()))
    with open(os.path.join(d, 'patch.json')) as fh:
        p = json.load(fh)
    rows = set(p.get('inputs', []))
    for dirpath, dirs, files in sorted(os.walk(inputs)):
        dirs.sort()
        for f in sorted(files):
            if f in rows:
                with open(os.path.join(dirpath, f), 'rb') as fh:
                    h.update(b'row\0%s\0%s\n' % (os.path.relpath(os.path.join(dirpath, f), inputs).encode(),
                                                   hashlib.sha256(fh.read()).hexdigest().encode()))
    h.update(b'stage\0%s\n' % str(p.get('stage')).encode())
    return h.hexdigest()


def edit_spec():
    """tools/edit-engine/src/spec.py as a module dict (runpy: python -I puts no tool folder on sys.path)."""
    import runpy
    return runpy.run_path(os.path.join(TOP, 'tools', 'edit-engine', 'src', 'spec.py'))


def file_problems(d, p):
    """Problems with patch.json "files" in patch folder D, and edits/*.json files it does not declare."""
    files = p.get('files')
    declared, bad = set(), []
    if files is not None:
        S = edit_spec()
        if not isinstance(files, list) or not files:
            return ['files must be a non-empty list']
        paths = set()
        for f in files:
            if not isinstance(f, dict) or set(f) - {'path', 'edits', 'origin', 'tree', 'install', 'source', 'input'} \
                    or ('edits' in f) == ('origin' in f):
                bad.append('files: each entry is {"path", "edits"} or {"path", "origin"} (+ "tree", "install"): %s'
                           % json.dumps(f))
                continue
            src = f.get('source')
            if src is not None and (not isinstance(src, str) or 'origin' not in f or not f['origin'].get('row')
                                    or src.startswith('/') or '..' in src.replace('!', '/').split('/')):
                bad.append('files: %s: source is a relative path inside the origin row ("ARCHIVE!MEMBER" for a '
                           'file in a tar), and needs an origin with a row' % f.get('path'))
            inp = f.get('input')
            if inp is not None and ('edits' not in f or not isinstance(inp, dict) or inp.get('kind') not in S['ORIGINS']
                                    or set(inp) not in ({'kind', 'path'}, {'kind', 'row'})
                                    or str(inp.get('path', '')).startswith('/')
                                    or ('row' in inp and inp['row'] not in p.get('inputs', []))):
                bad.append('files: %s: input is {"kind": %s, "path": RELATIVE} (the installed file an edits entry starts '
                           'from) or {"kind", "row": INPUT_ROW} (bytes from one of the patch inputs)'
                           % (f.get('path'), '|'.join(S['ORIGINS'])))
            if 'tree' in f and (f['tree'] is not True or 'edits' in f):
                bad.append('files: %s: "tree": true goes with an origin (every file under the folder)' % f.get('path'))
            inst = f.get('install')
            if inst is not None and (not isinstance(inst, dict) or set(inst) - {'mode', 'owner', 'flags'}
                                     or ('mode' in inst and not re.fullmatch(r'0[0-7]{3,4}', str(inst['mode'])))
                                     or ('owner' in inst and not re.fullmatch(r'[a-z_0-9]+:[a-z_0-9]+', str(inst['owner'])))
                                     or not isinstance(inst.get('flags', []), list)):
                bad.append('files: %s: install is {"mode": "0644", "owner": "root:wheel", "flags": [...]}'
                           % f.get('path'))
            path = f.get('path')
            if not (isinstance(path, str) and path and not path.startswith('/') and '..' not in path.split('/')):
                bad.append('files: path must be relative to the installed system: %r' % (path,))
                continue
            if path in paths:
                bad.append('files: %s declared twice' % path)
            paths.add(path)
            if 'origin' in f:
                o = f['origin']
                if not isinstance(o, dict) or o.get('kind') not in S['ORIGINS'] or set(o) - {'kind', 'name', 'row', 'note'}:
                    bad.append('files: %s: origin is {"kind": %s, "row", "name", "note"}' % (path, '|'.join(S['ORIGINS'])))
                elif 'row' in o and o['row'] not in p.get('inputs', []):
                    bad.append('files: %s: origin row %s is not one of the patch inputs' % (path, o['row']))
                continue
            rel = f['edits']
            if not (isinstance(rel, str) and rel.startswith('edits/') and rel.endswith('.json') and '..' not in rel):
                bad.append('files: %s: edits must be "edits/NAME.json"' % path)
                continue
            declared.add(rel)
            try:
                spec = S['load'](os.path.join(d, rel))
            except (OSError, ValueError) as e:
                bad.append('%s: %s' % (rel, e))
                continue
            bad += ['%s: %s' % (rel, problem) for problem in S['validate'](spec)]
            if spec.get('path') != path:
                bad.append('%s: names %s, patch.json files says %s' % (rel, spec.get('path'), path))
    folder = os.path.join(d, 'edits')
    for n in sorted(os.listdir(folder)) if os.path.isdir(folder) else []:
        if n.endswith('.json') and 'edits/' + n not in declared:
            bad.append('edits/%s is not declared in patch.json files' % n)
    return bad


def declared_files(root=os.path.join(TOP, 'patches')):
    """[(patch, revision, status, path, origin or None, spec or None)] for every declared file, in patch order."""
    S = edit_spec()
    out = []
    for name in patch_names(root):
        pj = os.path.join(directory(name, root), 'patch.json')
        if not os.path.isfile(pj):
            continue
        p = json.load(open(pj))
        for f in p.get('files', []):
            spec = S['load'](os.path.join(directory(name, root), f['edits'])) if 'edits' in f else None
            origin = dict(f['origin'], tree=True) if f.get('tree') else f.get('origin')
            out.append((name, p.get('revision'), p.get('status'), f['path'], origin, spec))
    return out


def explain(what=None, root=os.path.join(TOP, 'patches')):
    """Plain text: every declared file of patch WHAT, or every file whose path is or ends with /WHAT (all: None)."""
    S = edit_spec()
    rows = [r for r in declared_files(root)
            if what is None or r[0] == what or r[3] == what or r[3].endswith('/' + what.lstrip('/'))]
    if not rows:
        raise ValueError('no declared file or patch matches %r (patches declare files in patch.json "files")' % what)
    out = []
    for name, rev, _, path, origin, spec in rows:
        if out:
            out.append('')
        if spec:
            out += S['describe'](spec)
        else:
            out += [path, '  origin   %s' % S['origin_text'](origin)]
            if origin.get('note'):
                out.append('  note     %s' % origin['note'])
        out.append('  recipe   %s r%s' % (name, rev))
    return '\n'.join(out)


def sentence(text):
    text = text.strip()
    return text[:1].upper() + text[1:] + ('' if text.endswith('.') else '.')


def catalog(root=os.path.join(TOP, 'patches')):
    """docs/PATCHES.md: every declared file, its origin and each edit with its reason. Generated; never edited."""
    S = edit_spec()
    rows = declared_files(root)
    declaring = sorted({r[0] for r in rows})
    others = [n for n in patch_names(root) if n not in declaring and os.path.isfile(os.path.join(root, n, 'patch.json'))]
    md = ['# Patch catalog', '',
          'Generated by `python3.14 -I pipeline/patches.py catalog` from each patch.json `files` and its `edits/*.json`',
          '(edit specs for [tools/edit-engine](../tools/edit-engine/README.md)); the pipeline tests fail when it is stale.',
          '`python3.14 -I pipeline/patches.py explain NAME|PATH` prints the same for one patch or file.', '',
          '%d of %d patches declare their files so far.' % (len(declaring), len(declaring) + len(others)), '']
    for name in declaring:
        mine = [r for r in rows if r[0] == name]
        md += ['## %s' % name, '', 'Revision r%s, status %s. [Recipe](../patches/%s/README.md)' % (mine[0][1], mine[0][2], name), '']
        groups = {}
        for _, _, _, path, origin, spec in mine:
            if not spec:
                groups.setdefault(json.dumps(origin, sort_keys=True), []).append(path)
        large = {k: v for k, v in groups.items() if len(v) > 10}
        for k, paths in large.items():
            origin = json.loads(k)
            prefix = os.path.commonpath(paths) if len(paths) > 1 else os.path.dirname(paths[0])
            where = 'under `%s/`' % prefix if prefix else 'in several folders'
            md.append('- %d whole files from %s, %s (for example `%s`).%s Listed by `patches.py explain %s`.' % (
                len(paths), S['origin_text'](origin), where, os.path.relpath(paths[0], prefix) if prefix else paths[0],
                ' ' + sentence(origin['note']) if origin.get('note') else '', name))
        for _, _, _, path, origin, spec in mine:
            if not spec and json.dumps(origin, sort_keys=True) in large:
                continue
            if not spec:
                md.append('- `%s`: whole file from %s.%s' % (path, S['origin_text'](origin),
                                                              ' ' + sentence(origin['note']) if origin.get('note') else ''))
                continue
            sign = spec.get('sign')
            md += ['- `%s`: from %s; %d edit%s, applied by the %s%s' % (
                path, S['origin_text'](spec['origin']), len(spec['edits']), '' if len(spec['edits']) == 1 else 's',
                spec.get('applied_by', 'installer'), ', then signed: ' + S['sign_text'](sign) if sign else '')]
            for n, e in enumerate(spec['edits'], 1):
                line = '  %d. `%s`: %s' % (n, e['id'], S['edit_summary'](e).replace('|', '/'))
                if e['kind'] == 'literal':
                    line += ', `%s` → `%s`' % (S['spaced'](S['hexbytes'](e['find'])), S['spaced'](S['hexbytes'](e['replace'])))
                if e.get('asm'):
                    line += ' (`%s` → `%s`)' % (e['asm']['before'], e['asm']['after'])
                md += [line + '.', '     ' + sentence(e['reason'])]
        md.append('')
    md += ['## Not declared yet', '', ', '.join(others) if others else 'None.', '']
    return '\n'.join(md)


if __name__ == '__main__':
    if sys.argv[1:2] == ['path'] and len(sys.argv) == 3:
        print(directory(sys.argv[2], os.path.join(TOP, 'patches')))
        sys.exit(0)
    if sys.argv[1:2] == ['tracker']:
        out = sys.argv[2] if len(sys.argv) > 2 else os.path.join(TOP, 'patches', 'TRACKER.md')
        open(out, 'w').write(tracker())
        sys.exit(0)
    if sys.argv[1:2] == ['packs'] and len(sys.argv) == 3:
        defs = load_packs()
        for n in detect_packs(sys.argv[2], defs):
            print('%-22s %-13s %s' % (n, defs[n]['phase'], ', '.join(defs[n].get('recipes', [])) or '-'))
        sys.exit(0)
    if sys.argv[1:2] == ['digest'] and len(sys.argv) == 3:
        print(recipe_digest(sys.argv[2]))
        sys.exit(0)
    if sys.argv[1:2] == ['catalog']:
        out = sys.argv[2] if len(sys.argv) > 2 else os.path.join(TOP, 'docs', 'PATCHES.md')
        open(out, 'w').write(catalog())
        sys.exit(0)
    if sys.argv[1:2] == ['explain'] and len(sys.argv) <= 3:
        try:
            print(explain(sys.argv[2] if len(sys.argv) == 3 else None))
        except (ValueError, OSError) as e:
            sys.exit('patches.py: %s' % e)
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
    args, locks, skip, out, rest, pack_arg = [], None, [], None, iter(sys.argv[2:]), None
    for a in rest:
        if a not in ('--locks', '--skip-optional', '--record', '--packs'):
            args.append(a)
            continue
        value = next(rest, None)
        if value is None:
            sys.exit(__doc__)
        if a == '--locks':
            locks = [d for d in value.split(':') if d]
        elif a == '--skip-optional':
            skip.append(value)
        elif a == '--packs':
            pack_arg = value
        else:
            out = value
    if sys.argv[1:2] != ['plan'] or len(args) != 3 or any(a.startswith('--') for a in args):
        sys.exit(__doc__)
    try:
        record = {}
        selected = None if pack_arg is None else detect_packs(args[1]) if pack_arg == 'auto' else pack_arg.split(',')
        for stage, name, inputs in plan(*args, locks=locks if locks is not None else default_locks(args[2]),
                                        skip=skip, record=record, packs=selected):
            print(' '.join([stage, name] + inputs))
        if out:
            open(out, 'w').write(json.dumps(dict(record, skip_optional=skip), indent=1, sort_keys=True) + '\n')
    except (ValueError, OSError, KeyError) as e:
        sys.exit('patches.py: %s' % e)
