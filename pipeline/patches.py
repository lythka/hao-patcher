#!/usr/bin/env python3.14
"""patches.py: which patches of a set apply to a build, and at which stage.

  patches.py plan SET TARGET [OS/]BUILD [--locks DIR:DIR...] [--skip-optional GROUP]... [--record FILE] [--packs auto|A,B]
                                     one line per patch that applies, in set order: STAGE NAME [INPUT ...]
                                     (SET = pipeline/sets/SET.txt, TARGET = pipeline/targets/MODEL.json, BUILD the
                                     donor build and OS the macOS 27 build, e.g. 26B131/25G342; it stops when a
                                     recipe that would run does not meet its "requires"). Rows of
                                     optional patches are looked up in --locks (default pipeline/inputs/BUILD then
                                     pipeline/inputs/common); --record writes which optional patches ran or were skipped
                                     --packs: a recipe listed in a pack (packs/NAME.json) runs only when its pack is
                                     selected; auto detects them from the target (OCLP patch sets, board id)
  patches.py packs TARGET            the packs detected for TARGET, with their phase and recipes
  patches.py revisions NAME ...      NAME rN per patch, for the build record (MANIFEST.txt)
  patches.py tracker [OUT]           the patch tracker (patches/TRACKER.md): every patch, revision, sets, validation

  patches.py requires                every recipe's "requires" and "proven_on", or that it has none yet
  patches.py matrix [--json] [--check]
                                     plan only, every profile x target x (os, donor) pair of the assemblies: per cell
                                     the recipes that run, those "requires" refuses (and why), the running ones without
                                     proof there (proven_on), stale proof; --check exits 1 when a "release": true
                                     profile refuses a recipe on its own target and its assemblies' pairs
  patches.py check                   every patch against the patch format below; one line per problem, exit 1 if any
  patches.py release-gate [PROFILE ...]
                                     whether each release profile (default: every one) may ship: release_problems with
                                     the gate, no recipe "validated": false or status experiment unless
                                     pipeline/release-waivers.json holds an owner waiver for that profile, recipe and
                                     revision; exit 1 on any problem (release-cut always applies it)
  patches.py digest NAME             sha256 of everything that decides what NAME installs (for validated_digest)
  patches.py explain [NAME | PATH]   what each declared file comes from and every edit to it, with reasons (one patch,
                                     or every file whose path is or ends with /PATH); no argument: every patch
  patches.py catalog [OUT]           the patch catalog (docs/PATCHES.md): explain for every patch, as Markdown
  patches.py manifest [...]          what is installable, what each built image has and what is on the test laptops
                                     (docs/MANIFEST.md, and the records it is made from): pipeline/manifest.py

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
starts from names that file as "input": {"kind", "path"}. "install" "flags" are chflags names (restricted, schg,
uchg, sunlnk, hidden, nodump): declare what the hook sets, e.g. ["restricted"] where it runs `chflags restricted`.
A file made on the target (an origin without a row, or an external op) may name what it must come after there:
"after": [INSTALLED_PATH | "overlays"] ("overlays": every overlay file installed first). An edits file is an edit spec for
tools/edit-engine (format in its README) and must name the same path; every patches/NAME/edits/*.json must be declared.
"content": "seed" (the only value; absent means the bytes are the file) marks a file whose installed bytes are a
starting value that macOS legitimately changes after install (a Metal shader cache it appends to on first GPU use); it
needs "reason": a non-empty sentence saying who changes it and why. Both go into the recipe's install manifest
(pipeline/materialize.py, golden-image's manifests), where `hao verify` checks such a file's presence, type, mode, owner
and flags but not its content. "reason" only goes with "content".

"requires" (docs/requires.md) states where a recipe is valid, as data: {"os": ..., "donor": ..., "cpu": ..., "gpu": ...,
"firmware": ...}, every key present, each "any" or a list. os: macOS 27 builds (an assembly's golden_build); donor:
Tahoe donor builds (its donor_build; this replaces "applies"); gpu: GPU families (GPUS), at least one on the Mac;
firmware: the Mac's security chip (FIRMWARE), one of the list; cpu: CPU features, each present, or absent when written
!NAME. plan stops when a recipe that would run does not meet it. "proven_on": [{"model", "os", "donor" (optional),
"revision", "evidence"}] records which revision a boot proved where. Recipes in pipeline/requires-pending.txt have no
"requires" yet; the list only shrinks.
"""
import glob, hashlib, json, os, re, sys

def directory(name, root):
    return os.path.join(root, name)


def patch_names(root):
    return sorted(n for n in os.listdir(root) if os.path.isdir(os.path.join(root, n)))

TOP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STAGES = ('oclp-hook', 'post', 'recovery')
STATUSES = ('product', 'dev', 'diagnostic', 'experiment', 'unfinished', 'deprecated')
SHIPPING_SETS = ('recovery',)   # sets whose output goes to people: product patches only
APPLIES = ('models', 'builds')
# chflags names a declaration's install "flags" may use; tools/hao/src/livepatch.py FLAG_BITS sets each one
INSTALL_FLAGS = ('restricted', 'schg', 'uchg', 'sunlnk', 'hidden', 'nodump')


def names(setfile):
    """A set's recipe names: a set file (one name per line), or a profile (pipeline/profiles/NAME.json), generated."""
    if str(setfile).endswith('.json'):
        return generate(setfile)
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


REQUIRES = ('os', 'donor', 'cpu', 'gpu', 'firmware')
# GPU families (requires.gpu, a target's facts.gpu): vendor-generation; add one with its first recipe or target
GPUS = ('intel-snb', 'intel-ivb', 'intel-hsw', 'intel-bdw', 'intel-skl', 'intel-kbl', 'nvidia-tesla',
        'nvidia-kepler', 'amd-terascale', 'amd-gcn', 'amd-polaris', 'amd-vega', 'amd-navi')
FIRMWARE = ('none', 't1', 't2')        # the Mac's security chip (requires.firmware, a target's facts.firmware)
BUILD_ID = re.compile(r'\d\d[A-Z]\d{1,4}[a-z]?')
CPU_FEATURE = re.compile(r'!?[a-z0-9][a-z0-9_.]*')
PROVEN_KEYS = {'model', 'os', 'donor', 'revision', 'evidence'}
REQUIRES_PENDING = os.path.join(TOP, 'pipeline', 'requires-pending.txt')


def requires_problems(req):
    """What is wrong with a recipe's "requires" (empty: nothing)."""
    if not isinstance(req, dict):
        return ['requires must be an object with %s' % ', '.join(REQUIRES)]
    bad = []
    if set(req) != set(REQUIRES):
        bad.append('requires needs exactly the keys %s (each "any" or a list)' % ', '.join(REQUIRES))
    ok = dict(os=BUILD_ID.fullmatch, donor=BUILD_ID.fullmatch, cpu=CPU_FEATURE.fullmatch,
              gpu=lambda v: v in GPUS, firmware=lambda v: v in FIRMWARE)
    for k in REQUIRES:
        v = req.get(k)
        if v == 'any' or k not in req:
            continue
        if not (isinstance(v, list) and v and all(isinstance(x, str) for x in v)):
            bad.append('requires.%s must be "any" or a non-empty list' % k)
            continue
        wrong = [x for x in v if not ok[k](x)]
        if wrong:
            known = {'gpu': ' (one of %s)' % ', '.join(GPUS), 'firmware': ' (one of %s)' % ', '.join(FIRMWARE)}
            bad.append('requires.%s: %s not valid%s' % (k, ', '.join(wrong), known.get(k, '')))
    return bad


def proven_problems(proven, revision):
    if not isinstance(proven, list):
        return ['proven_on must be a list of {model, os, donor, revision, evidence}']
    bad = []
    for i, e in enumerate(proven):
        if not isinstance(e, dict) or set(e) - PROVEN_KEYS or not {'model', 'os', 'revision', 'evidence'} <= set(e):
            bad.append('proven_on[%d] needs model, os, revision, evidence (and may have donor), nothing else' % i)
            continue
        if not all(isinstance(e.get(k), str) and BUILD_ID.fullmatch(e[k]) for k in ('os', 'donor') if k in e):
            bad.append('proven_on[%d]: os/donor must be build ids' % i)
        if not (isinstance(e['revision'], int) and 1 <= e['revision'] <= (revision or 0)):
            bad.append('proven_on[%d]: revision must be this recipe\'s revision or an earlier one' % i)
        if not (isinstance(e['model'], str) and e['model'] and isinstance(e['evidence'], str) and e['evidence'].strip()):
            bad.append('proven_on[%d]: model and evidence must be named' % i)
    return bad


def target_facts(target):
    """What "requires" is checked against on TARGET (a targets/*.json path or its dict): model, cpu features (OCLP
    hardware profile flags and leafs, lower case; a target without that profile, the generic one, gives its flags as
    "facts" "cpu"), gpu families and firmware (the target's "facts")."""
    t = json.load(open(target)) if isinstance(target, (str, os.PathLike)) else target
    facts = t.get('facts', {})
    cpu = t['oclp_hardware_profile'].get('cpu', {}) if 'oclp_hardware_profile' in t else facts.get('cpu', {})
    return dict(model=t.get('model'), cpu={x.lower() for x in cpu.get('flags', []) + cpu.get('leafs', [])},
                gpu=set(facts.get('gpu', [])), firmware=facts.get('firmware'))


def unmet(req, facts, os_build, donor):
    """Why REQ is not met on FACTS with the two builds (empty: it is). An unknown fact never meets a requirement."""
    why = []
    for key, have in (('os', os_build), ('donor', donor)):
        if req[key] != 'any' and have not in req[key]:
            why.append('%s %s is not one of %s' % (key, have or '(not given: plan OS/BUILD)', ', '.join(req[key])))
    if req['gpu'] != 'any' and not facts['gpu'] & set(req['gpu']):
        why.append('gpu %s is not one of %s' % (', '.join(sorted(facts['gpu'])) or '(unknown)', ', '.join(req['gpu'])))
    if req['firmware'] != 'any' and facts['firmware'] not in req['firmware']:
        why.append('firmware %s is not one of %s' % (facts['firmware'] or '(unknown)', ', '.join(req['firmware'])))
    if req['cpu'] != 'any' and not facts['cpu']:
        why.append('cpu (unknown: the target has no OCLP cpu profile) cannot meet %s' % ', '.join(req['cpu']))
    for f in [] if req['cpu'] == 'any' or not facts['cpu'] else req['cpu']:
        name, absent = f.lstrip('!'), f.startswith('!')
        if (name in facts['cpu']) == absent:
            why.append('cpu %s %s' % (name, 'is present' if absent else 'is missing'))
    return why


def requires_tree_problems(root=os.path.join(TOP, 'patches'), pending=REQUIRES_PENDING, order=None):
    """The tree's rules for "requires": every recipe has it unless pending lists it; pending holds only recipes without
    it; a build id in a recipe's order.txt comment is in its requires.os or requires.donor."""
    bad, have = [], {}
    for name in patch_names(root):
        f = os.path.join(directory(name, root), 'patch.json')
        if os.path.isfile(f):
            have[name] = json.load(open(f)).get('requires')
    waiting = names(pending) if os.path.isfile(pending) else []
    for name, req in have.items():
        if req is None and name not in waiting:
            bad.append('%s: needs "requires" (docs/requires.md)' % name)
        if req is not None and name in waiting:
            bad.append('%s: has "requires": remove it from pipeline/requires-pending.txt' % name)
    bad += ['requires-pending.txt: %s is not a recipe' % n for n in waiting if n not in have]
    order = ORDER if order is None else order
    for line in open(order) if os.path.isfile(order) else []:
        name, _, comment = line.partition('#')
        req = have.get(name.strip())
        if not isinstance(req, dict):
            continue
        scope = set(req.get('os') or []) | set(req.get('donor') or []) if 'any' not in (req.get('os'), req.get('donor')) \
            else None
        for b in sorted(set(BUILD_ID.findall(comment))):
            if scope is not None and b not in scope:
                bad.append('order.txt: %s: the comment names %s, its requires does not' % (name.strip(), b))
    return bad


def requires_table(root=os.path.join(TOP, 'patches')):
    """One line per recipe: name, pack, its "requires" (or PENDING) and how many proven_on entries."""
    out = []
    for name in patch_names(root):
        f = os.path.join(directory(name, root), 'patch.json')
        if not os.path.isfile(f):
            continue
        p = json.load(open(f))
        req = p.get('requires')
        text = 'PENDING' if req is None else '  '.join('%s=%s' % (k, req.get(k) if req.get(k) == 'any' else
                                                                  ','.join(req.get(k) or [])) for k in REQUIRES)
        out.append('%-36s %-18s %s%s' % (name, p.get('pack', '?'), text,
                                         '  proven_on=%d' % len(p['proven_on']) if p.get('proven_on') else ''))
    return '\n'.join(out)


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
            # a file row's sha256, or a sealed folder row's content hash (lock.py row_sha256 does both)
            if sha != '-' and lock['row_hash'](d, row, p) != sha:
                raise ValueError('row %s: %s is not %s (a corrupted input stops, it is not skipped)' % (row, p, sha))
            return None
    return 'no row %s in %s' % (row, ', '.join(os.path.relpath(d, TOP) if d.startswith(TOP) else d for d in locks))


PACKS = os.path.join(TOP, 'packs')
PHASES = ('installer', 'live-capable')


def load_packs(packs_dir=PACKS, root=os.path.join(TOP, 'patches')):
    """{name: packs/NAME.json}, each with "recipes": the recipes whose patch.json names it ("pack"), in order.txt
    order (a recipe declares its pack; the pack file lists none, docs/layout.md)."""
    out = {}
    for n in sorted(os.listdir(packs_dir)) if os.path.isdir(packs_dir) else []:
        if n.endswith('.json'):
            with open(os.path.join(packs_dir, n)) as f:
                out[n[:-5]] = json.load(f)
    rank = {name: i for i, name in enumerate(order_names())}
    declared = {}
    for name in patch_names(root) if os.path.isdir(root) else []:
        f = os.path.join(directory(name, root), 'patch.json')
        pk = json.load(open(f)).get('pack') if os.path.isfile(f) else None
        if pk:
            declared.setdefault(pk, []).append(name)
    for pk, names in declared.items():
        if pk in out:
            listed = out[pk].get('recipes', [])
            out[pk]['recipes'] = listed + sorted((x for x in names if x not in listed),
                                                 key=lambda x: (rank.get(x, len(rank)), x))
    return out


ORDER = os.path.join(TOP, 'pipeline', 'order.txt')
PROFILES = os.path.join(TOP, 'pipeline', 'profiles')
TARGETS = os.path.join(TOP, 'pipeline', 'targets')
OCLP_PROFILES = os.path.join(TOP, 'tools', 'oclp-hardware', 'profiles')
ASSEMBLIES = os.path.join(TOP, 'pipeline', 'assemblies')


def set_path(name, top=TOP):
    """The set NAME: its profile (pipeline/profiles/NAME.json, generated) when there is one, else pipeline/sets/NAME.txt
    (the hand sets: legacy runners, the bootstrap and 26A428 sets)."""
    profile = os.path.join(str(top), 'pipeline', 'profiles', name + '.json')
    return profile if os.path.isfile(profile) else os.path.join(str(top), 'pipeline', 'sets', name + '.txt')


def order_names(path=ORDER):
    """Every image recipe once, in build order (pipeline/order.txt)."""
    return names(path) if os.path.isfile(path) else []


def generate(profile, root=os.path.join(TOP, 'patches'), packs=None, order=ORDER, targets=None):
    """An image's recipe list from a profile (pipeline/profiles/NAME.json): order.txt filtered to the packs the target
    detects (opt-in packs never), the packs and single opt-in recipes the profile names, and the recipes of the
    profile's variants, each replacing the recipe it names ("replaces"). A set is not build-specific: plan() leaves out,
    at build time, recipes whose applies exclude the model and build. {"target": T, "packs": [...], "recipes": [...],
    "variants": [...]}"""
    prof = json.load(open(profile)) if isinstance(profile, (str, os.PathLike)) else profile
    packs = load_packs(root=root) if packs is None else packs
    target = os.path.join(targets or TARGETS, prof['target'] + '.json')
    if not os.path.isfile(target):
        raise ValueError('profile names unknown target %s (no %s)' % (prof['target'], os.path.relpath(target, TOP)))
    selected = set(detect_packs(target, packs)) | set(prof.get('packs', []))
    unknown = [p for p in prof.get('packs', []) if p not in packs]
    if unknown:
        raise ValueError('profile names unknown packs: %s' % ', '.join(unknown))
    variants, named = set(prof.get('variants', [])), set(prof.get('recipes', []))
    out, replaced = [], set()
    for name in order_names(order):
        p = json.load(open(os.path.join(directory(name, root), 'patch.json')))
        if p.get('stage') == 'recovery':
            continue
        if not (p.get('pack') in selected or name in named):
            continue
        if p.get('variant') and p['variant'] not in variants:
            continue
        if p.get('variant') and p.get('replaces'):
            replaced.add(p['replaces'])
        out.append(name)
    return [n for n in out if n not in replaced]


def pack_problems(packs, root=os.path.join(TOP, 'patches')):
    """Problems with packs/*.json: keys, phase, detect rule, and each recipe in at most one pack."""
    bad, member = [], {}
    known = set(patch_names(root))
    for name, p in packs.items():
        where = 'packs/%s.json' % name
        unknown = set(p) - {'format', 'description', 'detect', 'oclp_patchsets', 'phase', 'proven_on', 'recipes',
                            'opt_in', 'layer', 'kind', 'platform'}
        if unknown:
            bad.append('%s: unknown keys %s' % (where, ', '.join(sorted(unknown))))
        if 'opt_in' in p and p['opt_in'] is not True:
            bad.append('%s: opt_in is true or absent' % where)
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
    return select_packs(oclp_patchsets(target), t.get('board_id'), packs)


def oclp_profile(target):
    """tools/oclp-hardware/profiles/NAME.json of TARGET (pipeline/targets/NAME.json)."""
    return os.path.join(OCLP_PROFILES, os.path.splitext(os.path.basename(str(target)))[0] + '.json')


def oclp_patchsets(target):
    """The OCLP patch sets of TARGET's hardware profile. A target without its profile, or a profile without
    "patchset_order", stops: without it every platform pack goes undetected and the image would get base only."""
    profile = oclp_profile(target)
    if not os.path.isfile(profile):
        raise ValueError('target %s has no OCLP hardware profile (%s): make it with tools/oclp-hardware before building'
                         % (os.path.basename(str(target)), os.path.relpath(profile, TOP)))
    with open(profile) as f:
        order = json.load(f).get('patchset_order')
    if not isinstance(order, list):
        raise ValueError('%s has no "patchset_order" list' % os.path.relpath(profile, TOP))
    return set(order)


def select_packs(patchsets, board_id, packs=None):
    """Names of the packs a Mac selects from its OCLP patch sets and board id (hao detect uses it on the target)."""
    packs = load_packs() if packs is None else packs
    out = []
    for name, p in packs.items():
        if p.get('opt_in'):
            continue                   # selected only by name (--packs NAME,...), never by detection
        d = p.get('detect', {})
        if d.get('any_intel') or set(patchsets) & set(d.get('oclp_patchsets', [])) or board_id in d.get('board_ids', []):
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
    facts = target_facts(target)
    os_build, _, build = str(build).rpartition('/') if '/' in str(build) else (None, '', build)
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
            ok = applies('any' if 'requires' in p else p.get('applies'), model, build)
        except ValueError as e:
            raise ValueError('%s: %s' % (name, e))
        if not ok:
            print('patches: %s does not apply to %s %s; skipped' % (name, model, build), file=sys.stderr)
            continue
        if packs is not None:
            defs = pack_defs if pack_defs is not None else load_packs()
            owner = next((pn for pn, pd in defs.items() if name in pd.get('recipes', [])), None)
            # an opt-in pack's recipe runs when the set (its profile) names it; detection never selects those packs
            if owner is not None and owner not in packs and not defs[owner].get('opt_in'):
                print('patches: %s is in pack %s, not selected; skipped' % (name, owner), file=sys.stderr)
                continue
        if 'requires' in p:
            why = unmet(p['requires'], facts, os_build, build)
            if why:
                raise ValueError('%s: requires not met on %s (%s/%s): %s' % (name, model, os_build, build, '; '.join(why)))
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


def read_json(path):
    with open(path) as f:
        return json.load(f)


def build_pairs(assemblies=ASSEMBLIES):
    """{(golden_build, donor_build): [the patch_set of each assembly with both keys]}."""
    pairs = {}
    for n in sorted(os.listdir(assemblies)) if os.path.isdir(assemblies) else []:
        if n.endswith('.json'):
            a = read_json(os.path.join(assemblies, n))
            if a.get('golden_build') and a.get('donor_build'):
                pairs.setdefault((a['golden_build'], a['donor_build']), []).append(a.get('patch_set'))
    return pairs


def proof(p, model, os_build, donor):
    """("proven", N) when a proven_on entry for MODEL and OS_BUILD (and DONOR, when the entry names one) proves this
    revision; ("stale", N) when the newest such entry proves an older revision N; ("unproven", None) otherwise."""
    revs = [e.get('revision') for e in p.get('proven_on') or [] if isinstance(e, dict) and e.get('model') == model
            and e.get('os') == os_build and e.get('donor', donor) == donor and isinstance(e.get('revision'), int)]
    if not revs:
        return 'unproven', None
    return ('proven' if max(revs) >= (p.get('revision') or 0) else 'stale'), max(revs)


def matrix(profiles=PROFILES, targets=TARGETS, assemblies=ASSEMBLIES, root=os.path.join(TOP, 'patches'), packs=None,
           order=ORDER):
    """Plan-only cells for every profile x target x (os, donor) build pair of the assemblies: which recipes run, which
    "requires" refuses (with unmet()'s reasons), which running recipes have no proof there (proven_on for the target's
    model and the os build) or only an older revision's. Nothing stops the table: every recipe is evaluated."""
    packs = load_packs(root=root) if packs is None else packs
    pairs = build_pairs(assemblies)
    tnames = sorted(n[:-5] for n in os.listdir(targets) if n.endswith('.json')) if os.path.isdir(targets) else []
    cells = []
    for f in sorted(os.listdir(profiles)) if os.path.isdir(profiles) else []:
        if not f.endswith('.json'):
            continue
        prof, pname = read_json(os.path.join(profiles, f)), f[:-5]
        for tname in tnames:
            tpath = os.path.join(targets, tname + '.json')
            facts = target_facts(tpath)
            try:
                recipes, error = generate(dict(prof, target=tname), root, packs, order, targets), None
            except (ValueError, KeyError, OSError) as e:
                recipes, error = [], str(e)
            for (os_build, donor), sets in sorted(pairs.items()):
                cell = dict(profile=pname, release=prof.get('release') is True, target=tname, model=facts['model'],
                            os=os_build, donor=donor, own=tname == prof.get('target') and pname in sets, error=error,
                            run=[], refused={}, skipped={}, unproven=[], stale={}, host='unrecorded')
                for name in recipes:
                    try:
                        p = read_json(os.path.join(directory(name, root), 'patch.json'))
                        why = unmet(p['requires'], facts, os_build, donor) if 'requires' in p else []
                        if 'requires' not in p and not applies(p.get('applies'), facts['model'], donor):
                            cell['skipped'][name] = 'applies excludes %s %s' % (facts['model'], donor)
                            continue
                    except (ValueError, KeyError, TypeError, OSError) as e:
                        why = [str(e)]
                    if why:
                        cell['refused'][name] = why
                        continue
                    cell['run'].append(name)
                    state, rev = proof(p, facts['model'], os_build, donor)
                    if state != 'proven':
                        cell['unproven'].append(name)
                    if state == 'stale':
                        cell['stale'][name] = 'proven r%d, now r%s' % (rev, p.get('revision'))
                cells.append(cell)
        if prof.get('release') is True and not any(pname in s for s in pairs.values()):
            cells.append(dict(profile=pname, release=True, target=prof.get('target'), own=True, os=None, donor=None,
                              error='no assembly builds this release profile', run=[], refused={}, skipped={},
                              unproven=[], stale={}, host='unrecorded', model=None))
    return cells


def matrix_problems(cells):
    """A release profile must plan cleanly on its own target and its assemblies' build pairs."""
    bad = []
    for c in cells:
        if c['release'] and c['own'] and (c['error'] or c['refused']):
            where = '%s on %s %s/%s' % (c['profile'], c['target'], c['os'], c['donor'])
            bad += ['release %s: %s' % (where, c['error'])] if c['error'] else []
            bad += ['release %s: %s refused: %s' % (where, n, '; '.join(w)) for n, w in c['refused'].items()]
    return bad


def matrix_text(cells):
    out = []
    for c in cells:
        out.append('%-28s %-8s %s/%s  %3d run  %3d refused  %3d unproven  host %s%s%s%s' % (
            c['profile'] + (' (release)' if c['release'] else ''), c['target'], c['os'], c['donor'], len(c['run']),
            len(c['refused']), len(c['unproven']), c['host'], '  [own]' if c['own'] else '',
            '  (%d not applicable)' % len(c['skipped']) if c['skipped'] else '',
            '  ERROR %s' % c['error'] if c['error'] else ''))
        out += ['    refused %s: %s' % (n, '; '.join(w)) for n, w in c['refused'].items()]
        out += ['    stale %s: %s' % (n, w) for n, w in c['stale'].items()]
    return '\n'.join(out)


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
                            'files', 'validated_revision', 'validated_digest', 'pack', 'variant', 'replaces',
                            'requires', 'proven_on', 'input_reads'}
        if 'input_reads' in p:
            # the only parts of a folder row the recipe reads (pipeline/incremental.py fingerprints those alone)
            bad += ['%s: %s' % (name, x) for x in incremental()['reads_problems'](p)]
        if 'requires' in p:
            bad += ['%s: %s' % (name, x) for x in requires_problems(p['requires'])]
            if 'applies' in p:
                bad.append('%s: "requires" replaces "applies": its builds are requires.donor' % name)
        if 'proven_on' in p:
            bad += ['%s: %s' % (name, x) for x in proven_problems(p['proven_on'], p.get('revision'))]
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
        bad += dev_problems(load_packs(), root, sets_dir)
        bad += layout_problems(root)
        bad += requires_tree_problems(root)
        bad += release_profile_problems(root)
        bad += waiver_problems(root)
    return bad


RELEASE_REFUSED_STATUSES = ('dev', 'diagnostic')   # never shipped: bench and logging-only recipes
RELEASE_GATED_STATUSES = ('experiment',)           # shipped only with an owner waiver, like validated: false
WAIVERS = os.path.join(TOP, 'pipeline', 'release-waivers.json')
WAIVER_REASON = re.compile(r'\bowner (\d{4}-\d{2}-\d{2})\b')


def load_waivers(path=WAIVERS):
    """pipeline/release-waivers.json's waivers (none when the file is not there)."""
    return json.load(open(path)).get('waivers', []) if os.path.isfile(path) else []


def release_problems(name, root=os.path.join(TOP, 'patches'), packs=None, profiles=PROFILES, order=ORDER,
                     gate=True, waivers=None, dev=False):
    """Why the set NAME may not be released (empty: it may): it must be a profile marked "release": true, and none of
    its recipes may come from a dev-layer pack or have a status in RELEASE_REFUSED_STATUSES. With gate (release-cut,
    patches.py release-gate), none may be "validated": false or have a status in RELEASE_GATED_STATUSES either, unless
    a waiver (pipeline/release-waivers.json) names this profile, that recipe and its current revision. With dev (a dev
    candidate, release-cut --candidate --dev; never published) the profile must not be a release profile, and its
    dev-layer and dev/diagnostic recipes are allowed; the gate still applies."""
    f = os.path.join(profiles, name + '.json')
    if not os.path.isfile(f):
        return ['set %s: not a profile (pipeline/profiles/%s.json); only release profiles are released' % (name, name)]
    is_release = json.load(open(f)).get('release') is True
    if dev and is_release:
        return ['profile %s: a release profile is cut without --dev' % name]
    if not dev and not is_release:
        return ['profile %s: not marked "release": true' % name]
    packs = packs if packs is not None else load_packs(root=root)
    waivers = load_waivers() if waivers is None else waivers
    layer = {r: pk.get('layer') for pk in packs.values() for r in pk.get('recipes', [])}
    bad = []
    for r in generate(f, root, packs, order):
        pj = os.path.join(directory(r, root), 'patch.json')
        p = json.load(open(pj)) if os.path.isfile(pj) else {}
        status = p.get('status')
        if layer.get(r) == 'dev' and not dev:
            bad.append('profile %s: %s is in a dev-layer pack' % (name, r))
        if status in RELEASE_REFUSED_STATUSES and not dev:
            bad.append('profile %s: %s has status %s' % (name, r, status))
        if not gate:
            continue
        why = (['validated false'] if p.get('validated') is not True else []) + \
              (['status %s' % status] if status in RELEASE_GATED_STATUSES else [])
        if why and not any(w.get('profile') == name and w.get('recipe') == r and w.get('revision') == p.get('revision')
                           for w in waivers):
            bad.append('profile %s: %s r%s is %s (no owner waiver in pipeline/release-waivers.json)'
                       % (name, r, p.get('revision'), ' and '.join(why)))
    return bad


def waiver_problems(root=os.path.join(TOP, 'patches'), packs=None, profiles=PROFILES, order=ORDER, waivers=None):
    """pipeline/release-waivers.json: each waiver names a release profile, a recipe of it and that recipe's current
    revision (a new revision needs a new waiver; a stale one must go), and the owner's decision with its date."""
    try:
        waivers = load_waivers() if waivers is None else waivers
    except ValueError as e:
        return ['pipeline/release-waivers.json: %s' % e]
    bad, seen = [], set()
    for i, w in enumerate(waivers):
        if not isinstance(w, dict):
            bad.append('waiver %d: not an object' % i)
            continue
        label = 'waiver %d (%s/%s)' % (i, w.get('profile'), w.get('recipe'))
        missing = [k for k in ('profile', 'recipe', 'revision', 'reason', 'decided') if k not in w]
        if missing:
            bad.append('%s: has no %s' % (label, ', '.join(missing)))
            continue
        if (w['profile'], w['recipe']) in seen:
            bad.append('%s: named twice' % label)
        seen.add((w['profile'], w['recipe']))
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', str(w['decided'])):
            bad.append('%s: decided %r is not YYYY-MM-DD' % (label, w['decided']))
        m = WAIVER_REASON.search(str(w['reason']))
        if not m or m.group(1) != w['decided']:
            bad.append('%s: reason must be the owner\'s decision with its date ("owner %s: ...")' % (label, w['decided']))
        f = os.path.join(profiles, str(w['profile']) + '.json')
        if not os.path.isfile(f) or json.load(open(f)).get('release') is not True:
            bad.append('%s: %s is not a release profile' % (label, w['profile']))
            continue
        pj = os.path.join(directory(str(w['recipe']), root), 'patch.json')
        if not os.path.isfile(pj):
            bad.append('%s: no recipe %s' % (label, w['recipe']))
            continue
        if w['recipe'] not in generate(f, root, packs if packs is not None else load_packs(root=root), order):
            bad.append('%s: %s is not in profile %s' % (label, w['recipe'], w['profile']))
            continue
        p = json.load(open(pj))
        if w['revision'] != p.get('revision'):
            bad.append('%s: waives r%s but the recipe is r%s (stale: remove it, or the owner waives r%s)'
                       % (label, w['revision'], p.get('revision'), p.get('revision')))
        elif p.get('validated') is True and p.get('status') not in RELEASE_GATED_STATUSES:
            bad.append('%s: r%s is validated and not an experiment; the waiver is not needed' % (label, w['revision']))
    return bad


def release_profile_problems(root=os.path.join(TOP, 'patches'), packs=None, profiles=PROFILES, order=ORDER,
                             gate=False, names=None, waivers=None):
    """release_problems of every profile marked "release": true (or of NAMES); patches.py check runs it without the
    validated/experiment gate, patches.py release-gate with it."""
    out = []
    if names is None:
        names = [os.path.basename(f)[:-5] for f in sorted(glob.glob(os.path.join(profiles, '*.json')))
                 if json.load(open(f)).get('release') is True]
    for n in names:
        out += release_problems(n, root, packs, profiles, order, gate=gate, waivers=waivers)
    return out


LAYERS = ('os', 'platform', 'dev')
KINDS = ('model', 'cpu', 'gpu', 'wifi', 'audio', 'input', 'recovery')


PLATFORM_DATA_KEY = re.compile(r'[a-z][a-z0-9_]*\Z')   # the keys an edit spec's when_target names (tools/edit-engine)


def target_problems(t):
    """Problems with a target (pipeline/targets/*.json, as a dict): a "model", and "platform_data", when present, an
    object of KEY: any JSON value (data that is the platform's, not the OS layer's; docs/layout.md), each KEY lower
    case. Hooks get it as HAO_TARGET_DATA; an edit spec's "when_target" names one of its keys."""
    bad = []
    if not (isinstance(t.get('model'), str) and t['model']):
        bad.append('needs "model"')
    data = t.get('platform_data', {})
    if not isinstance(data, dict):
        bad.append('platform_data must be an object {KEY: VALUE}')
    else:
        bad += ['platform_data: key %r must be lower-case letters, digits and _' % k
                for k in data if not PLATFORM_DATA_KEY.match(k)]
    return bad


def layout_problems(root=os.path.join(TOP, 'patches'), packs_dir=PACKS, order=ORDER, profiles=PROFILES):
    """docs/layout.md: every recipe declares one pack; a pack declares its layer (a platform pack its kind and
    platform) and lists no recipes; order.txt holds every image recipe once; a variant replaces a recipe of the same
    pack; every profile generates."""
    bad = []
    raw = {}
    for n in sorted(os.listdir(packs_dir)) if os.path.isdir(packs_dir) else []:
        if n.endswith('.json'):
            raw[n[:-5]] = json.load(open(os.path.join(packs_dir, n)))
    for name, d in raw.items():
        if 'recipes' in d:
            bad.append('pack %s: lists recipes; each recipe declares its pack ("pack" in patch.json)' % name)
        if d.get('layer') not in LAYERS:
            bad.append('pack %s: needs "layer": %s' % (name, ' | '.join(LAYERS)))
        if d.get('layer') == 'platform' and (d.get('kind') not in KINDS or not d.get('platform')):
            bad.append('pack %s: a platform pack needs "kind" (%s) and "platform"' % (name, ', '.join(KINDS)))
    for f in sorted(os.listdir(os.path.join(TOP, 'pipeline', 'targets'))):
        if f.endswith('.json'):
            try:
                oclp_patchsets(os.path.join(TOP, 'pipeline', 'targets', f))
                bad += ['%s: %s' % (f, p) for p in target_problems(json.load(open(os.path.join(TOP, 'pipeline', 'targets', f))))]
            except (ValueError, OSError) as x:
                bad.append(str(x))
    ordered = order_names(order)
    image = []
    for name in patch_names(root):
        f = os.path.join(directory(name, root), 'patch.json')
        if not os.path.isfile(f):
            continue
        p = json.load(open(f))
        if p.get('pack') not in raw:
            bad.append('%s: needs "pack" naming a packs/*.json (docs/layout.md)' % name)
        if p.get('stage') != 'recovery':
            image.append(name)
        if 'replaces' in p and 'variant' not in p:
            bad.append('%s: "replaces" goes with "variant"' % name)
        if p.get('replaces'):
            other = os.path.join(directory(p['replaces'], root), 'patch.json') if p['replaces'] in patch_names(root) else None
            if other is None:
                bad.append('%s: replaces unknown recipe %s' % (name, p['replaces']))
            elif json.load(open(other)).get('pack') != p.get('pack'):
                bad.append('%s: replaces %s of another pack' % (name, p['replaces']))
    if ordered:
        bad += ['order.txt: %s is not an image recipe' % n for n in ordered if n not in image]
        bad += ['order.txt: %s is missing' % n for n in image if n not in ordered]
        bad += ['order.txt: %s is listed twice' % n for n in sorted({n for n in ordered if ordered.count(n) > 1})]
    for f in sorted(os.listdir(profiles)) if os.path.isdir(profiles) else []:
        if f.endswith('.json'):
            try:
                generate(os.path.join(profiles, f), root)
            except (ValueError, KeyError, OSError) as x:
                bad.append('profile %s: %s' % (f, x))
    return bad


def is_dev_set(name):
    """Sets that may hold status-dev recipes: bench images (dev-*) and the legacy Tahoe bench set (*-dev)."""
    return name.startswith('dev-') or name.endswith('-dev')


def dev_problems(packs, root=os.path.join(TOP, 'patches'), sets_dir=os.path.join(TOP, 'pipeline', 'sets')):
    """An end-user image carries nothing of a bench image's: every status-dev recipe is in an opt-in pack, only dev
    sets hold one, and no other recipe switches on a build-wide dev flag (IMAGE_DEV_BUILD is gone)."""
    bad = []
    opt_in = {r for p in packs.values() if p.get('opt_in') for r in p.get('recipes', [])}
    dev = set()
    for name in patch_names(root):
        f = os.path.join(directory(name, root), 'patch.json')
        if os.path.isfile(f) and json.load(open(f)).get('status') == 'dev':
            dev.add(name)
            if name not in opt_in:
                bad.append('%s: status dev, but in no opt-in pack (packs/dev.json)' % name)
    for s in sorted(os.listdir(sets_dir)) if os.path.isdir(sets_dir) else []:
        if s.endswith('.txt') and not is_dev_set(s[:-4]):
            for name in names(os.path.join(sets_dir, s)):
                if name in dev:
                    bad.append('set %s: %s is a dev recipe; only dev-* sets may hold one' % (s[:-4], name))
    for name in patch_names(root):
        if name in opt_in:
            continue
        for dirpath, dirs, files in os.walk(directory(name, root)):
            dirs[:] = [x for x in dirs if x not in ('tests', '__pycache__', 'build')]
            for fn in files:
                if fn.endswith(('.sh', '.py')):
                    with open(os.path.join(dirpath, fn), errors='replace') as fh:
                        if 'IMAGE_DEV_BUILD' in fh.read():
                            bad.append('%s: %s reads IMAGE_DEV_BUILD; dev-only content belongs in pack dev'
                                       % (name, os.path.relpath(os.path.join(dirpath, fn), root)))
    return bad

DIGEST_SKIP = {'README.md', 'patch.json', 'edits', 'tests'}


def incremental():
    """pipeline/incremental.py as a module dict."""
    import runpy
    return runpy.run_path(os.path.join(TOP, 'pipeline', 'incremental.py'))


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
            # read-from-share.txt says only where the assembly reads an input from (the kit, not a copy): same bytes
            if (rel == '.' and f in DIGEST_SKIP) or f.endswith('.pyc') or f in ('.DS_Store', 'read-from-share.txt'):
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


SEED = 'seed'


def seed_problems(f):
    """Problems with a "files" entry F's "content" and "reason": content is "seed" or absent, a seed needs a non-empty
    reason, and a reason goes only with content."""
    if 'content' not in f:
        return ['files: %s: "reason" goes with "content": "seed"' % f.get('path')] if 'reason' in f else []
    if f['content'] != SEED:
        return ['files: %s: content is "seed" (bytes macOS changes after install) or absent, not %r'
                % (f.get('path'), f['content'])]
    r = f.get('reason')
    if not isinstance(r, str) or not r.strip():
        return ['files: %s: "content": "seed" needs a "reason" (who changes the file after install, and why)'
                % f.get('path')]
    return []


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
            if not isinstance(f, dict) or set(f) - {'path', 'edits', 'origin', 'tree', 'install', 'source', 'input',
                                                    'after', 'content', 'reason'} \
                    or ('edits' in f) == ('origin' in f):
                bad.append('files: each entry is {"path", "edits"} or {"path", "origin"} (+ "tree", "install"): %s'
                           % json.dumps(f))
                continue
            after = f.get('after')
            if after is not None and (not isinstance(after, list) or not after or not all(
                    isinstance(a, str) and (a == 'overlays' or (a and not a.startswith('/') and '..' not in a.split('/')))
                    for a in after)):
                bad.append('files: %s: after is a list of installed paths (relative) or "overlays" (every overlay file '
                           'installed first), what a file made on the target must come after' % f.get('path'))
            bad += seed_problems(f)
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
                                     or not isinstance(inst.get('flags', []), list)
                                     or set(inst.get('flags', [])) - set(INSTALL_FLAGS)):
                bad.append('files: %s: install is {"mode": "0644", "owner": "root:wheel", "flags": [%s]}'
                           % (f.get('path'), ', '.join(INSTALL_FLAGS)))
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
    """[(patch, revision, status, path, origin or None, spec or None, entry)] for every declared file, in patch order
    (entry: the patch.json "files" entry itself, for its "install" and "after")."""
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
            out.append((name, p.get('revision'), p.get('status'), f['path'], origin, spec, f))
    return out


# What the materialiser writes for a file whose declaration has no "install" (pipeline/materialize.py).
DEFAULT_INSTALL = 'root:wheel, 0755 for a Mach-O, 0644 otherwise, no flags (a tar member keeps its own mode and owner)'


def install_text(install):
    """'mode 0644, owner root:wheel, flags restricted' for a declaration's "install" (None: nothing declared)."""
    if not install:
        return None
    parts = ['mode %s' % install['mode']] if install.get('mode') else []
    parts += ['owner %s' % install['owner']] if install.get('owner') else []
    parts += ['flags %s' % ', '.join(install['flags'])] if install.get('flags') else []
    return ', '.join(parts) or 'no attributes'


def after_list(entry, spec, S):
    """[(installed path or "overlays", why or None)]: what the file is made after on the target, the installed files its
    spec's external ops read, then the entry's "after", as pipeline/materialize.py orders deferred files."""
    out = []
    for e in (spec or {}).get('edits', []):
        if e.get('kind') == 'op':
            for arg in S['EXTERNAL_INPUTS'].get(e['op'], ()):
                if e.get('args', {}).get(arg):
                    out.append((e['args'][arg], '%s reads it' % e['op']))
    return out + [(a, None) for a in entry.get('after') or []]


def after_text(item, code=False):
    path, why = item
    if path == 'overlays':
        return 'every overlay file installed first'
    return ('`%s`' % path if code else path) + (' (%s)' % why if why else '')


def explain(what=None, root=os.path.join(TOP, 'patches')):
    """Plain text: every declared file of patch WHAT, or every file whose path is or ends with /WHAT (all: None)."""
    S = edit_spec()
    rows = [r for r in declared_files(root)
            if what is None or r[0] == what or r[3] == what or r[3].endswith('/' + what.lstrip('/'))]
    if not rows:
        raise ValueError('no declared file or patch matches %r (patches declare files in patch.json "files")' % what)
    out = []
    for name, rev, _, path, origin, spec, entry in rows:
        if out:
            out.append('')
        if spec:
            out += S['describe'](spec)
        else:
            out += [path, '  origin   %s' % S['origin_text'](origin)]
            if origin.get('note'):
                out.append('  note     %s' % origin['note'])
        out.append('  install  %s' % (install_text(entry.get('install')) or 'default: ' + DEFAULT_INSTALL))
        if entry.get('content') == SEED:
            out.append('  content  seed: %s' % entry.get('reason'))
        after = after_list(entry, spec, S)
        if after:
            out.append('  after    %s' % '; '.join(after_text(i) for i in after))
        out.append('  recipe   %s r%s' % (name, rev))
    return '\n'.join(out)


def sentence(text):
    text = text.strip()
    return text[:1].upper() + text[1:] + ('' if text.endswith('.') else '.')


def attributes(entry, spec, S):
    """' Installed mode 0644, owner root:wheel. Made after `X`.' for a catalog line ('' when the entry has neither)."""
    out = ''
    if entry.get('content') == SEED:
        out += ' Seed (macOS changes it after install): %s' % sentence(entry.get('reason', ''))
    if install_text(entry.get('install')):
        out += ' Installed %s.' % install_text(entry['install'])
    after = after_list(entry, spec, S)
    if after:
        out += ' Made after %s.' % ', '.join(after_text(i, code=True) for i in after)
    return out


def catalog(root=os.path.join(TOP, 'patches')):
    """docs/PATCHES.md: every declared file, its origin and each edit with its reason. Generated; never edited."""
    S = edit_spec()
    rows = declared_files(root)
    declaring = sorted({r[0] for r in rows})
    others = [n for n in patch_names(root) if n not in declaring and os.path.isfile(os.path.join(root, n, 'patch.json'))]
    md = ['# Patch catalog', '',
          'Generated by `python3.14 -I pipeline/patches.py catalog` from each patch.json `files` and its `edits/*.json`',
          '(edit specs for [tools/edit-engine](../tools/edit-engine/README.md)); the pipeline tests fail when it is stale.',
          '`python3.14 -I pipeline/patches.py explain NAME|PATH` prints the same for one patch or file.',
          'A file is installed with the attributes its declaration gives (*installed*: mode, owner, chflags); one',
          'without them gets %s.' % DEFAULT_INSTALL,
          'A file made on the target lists what it is made *after* there. A *seed* file is a starting value macOS',
          'changes after install (`"content": "seed"` with a `"reason"`): `hao verify` checks it is there with its',
          'attributes, not its bytes.', '',
          '%d of %d patches declare their files so far.' % (len(declaring), len(declaring) + len(others)), '']
    for name in declaring:
        mine = [r for r in rows if r[0] == name]
        md += ['## %s' % name, '', 'Revision r%s, status %s. [Recipe](../patches/%s/README.md)' % (mine[0][1], mine[0][2], name), '']
        groups = {}
        key = lambda origin, entry: json.dumps([origin, entry.get('install'), entry.get('after'), entry.get('content'),
                                                entry.get('reason')], sort_keys=True)
        for _, _, _, path, origin, spec, entry in mine:
            if not spec:
                groups.setdefault(key(origin, entry), []).append(path)
        large = {k: v for k, v in groups.items() if len(v) > 10}
        for k, paths in large.items():
            origin, install, after, content, reason = json.loads(k)
            prefix = os.path.commonpath(paths) if len(paths) > 1 else os.path.dirname(paths[0])
            where = 'under `%s/`' % prefix if prefix else 'in several folders'
            md.append('- %d whole files from %s, %s (for example `%s`).%s%s Listed by `patches.py explain %s`.' % (
                len(paths), S['origin_text'](origin), where, os.path.relpath(paths[0], prefix) if prefix else paths[0],
                ' ' + sentence(origin['note']) if origin.get('note') else '',
                attributes({'install': install, 'after': after, 'content': content, 'reason': reason}, None, S), name))
        for _, _, _, path, origin, spec, entry in mine:
            if not spec and key(origin, entry) in large:
                continue
            if not spec:
                md.append('- `%s`: whole file from %s.%s%s' % (path, S['origin_text'](origin),
                                                                ' ' + sentence(origin['note']) if origin.get('note') else '',
                                                                attributes(entry, None, S)))
                continue
            sign = spec.get('sign')
            md += ['- `%s`: from %s; %d edit%s, applied by the %s%s%s.%s' % (
                path, S['origin_text'](spec['origin']), len(spec['edits']), '' if len(spec['edits']) == 1 else 's',
                spec.get('applied_by', 'installer'),
                ' on targets whose platform_data has `%s`' % spec['when_target'] if spec.get('when_target') else '',
                ', then signed: ' + S['sign_text'](sign) if sign else '',
                attributes(entry, spec, S))]
            for n, e in enumerate(spec['edits'], 1):
                line = '  %d. `%s`: %s' % (n, e['id'], S['edit_summary'](e).replace('|', '/'))
                if e['kind'] == 'literal':
                    line += ', `%s` → `%s`' % (S['spaced'](S['hexbytes'](e['find'])), S['spaced'](S['hexbytes'](e['replace'])))
                if e.get('asm'):
                    line += ' (`%s` → `%s`)' % (e['asm']['before'], e['asm']['after'])
                md += [line + '.', '     ' + sentence(e['reason'])]
                md += ['     - `%s`: %s' % (arg, why.rstrip('.').replace('|', '/') + '.')
                       for arg, why in e.get('arg_reasons', {}).items()]
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
    if sys.argv[1:2] == ['set'] and len(sys.argv) in (3, 4):
        text = ''.join(n + '\n' for n in generate(sys.argv[2]))
        if len(sys.argv) == 4:
            open(sys.argv[3], 'w').write(text)
        else:
            sys.stdout.write(text)
        sys.exit(0)
    if sys.argv[1:2] == ['digest'] and len(sys.argv) == 3:
        print(recipe_digest(sys.argv[2]))
        sys.exit(0)
    if sys.argv[1:2] == ['catalog']:
        out = sys.argv[2] if len(sys.argv) > 2 else os.path.join(TOP, 'docs', 'PATCHES.md')
        open(out, 'w').write(catalog())
        sys.exit(0)
    if sys.argv[1:2] == ['manifest']:
        import runpy
        sys.exit(runpy.run_path(os.path.join(TOP, 'pipeline', 'manifest.py'))['main'](sys.argv[2:]))
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
    if sys.argv[1:2] == ['matrix'] and set(sys.argv[2:]) <= {'--json', '--check'}:
        cells = matrix()
        print(json.dumps(cells, indent=1) if '--json' in sys.argv else matrix_text(cells))
        problems = matrix_problems(cells) if '--check' in sys.argv else []
        if problems:
            print('\n'.join(problems), file=sys.stderr)
        sys.exit(1 if problems else 0)
    if sys.argv[1:2] == ['release-gate']:
        problems = release_profile_problems(gate=True, names=sys.argv[2:] or None) + waiver_problems()
        print('\n'.join(problems) or 'release-gate: every release profile may ship')
        sys.exit(1 if problems else 0)
    if sys.argv[1:2] == ['requires']:
        print(requires_table())
        sys.exit(0)
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
        for stage, name, inputs in plan(*args, locks=locks if locks is not None else default_locks(args[2].rpartition('/')[2]),
                                        skip=skip, record=record, packs=selected):
            print(' '.join([stage, name] + inputs))
        if out:
            open(out, 'w').write(json.dumps(dict(record, skip_optional=skip), indent=1, sort_keys=True) + '\n')
    except (ValueError, OSError, KeyError) as e:
        sys.exit('patches.py: %s' % e)
