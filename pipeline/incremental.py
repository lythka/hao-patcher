#!/usr/bin/env python3.14
"""incremental: what an image build can take from an earlier build of the same assembly, and from where it must run.

  incremental.py plan PREVIOUS NEW [--points DIR] [--json]
  incremental.py show RECORD

PREVIOUS is the earlier build: its record folder (store/runs/golden-*), its build.json, or the image repository it
committed (the newest committed record that names that repository, under store/runs). NEW is the record of the build
to make (a staged kit's record will do: golden-run --stage-kit records the same fingerprints). DIR holds the saved
points (default store/cache/golden-points).

golden-run records `fingerprints` in every build.json, from the staged kit, before the guest starts:
  base     what every step before the set's recipes reads: the assembly (less the keys that decide no byte), the
           rows that are not recipe inputs plus the recipe rows the base layers read (the Golden Gate payload, the
           kernel), the kit's small records (MANIFEST.txt, profile.json, target-data.json, optional.json), the
           assembler's code (SHARE/assembly-source), the staging code, and guest-config.json without the recipe lists
  recipes  per set entry, in set order: stage, name, staged folder, the staged code (the folder without inputs/,
           so the tools a make.sh copies in count) and each input row's identity (lock.py row_identity), or, for a
           row the recipe declares it reads only in part (patch.json "input_reads"), the digest of those parts

plan() compares the two: a different base is a full build; else the first set entry that differs is where the build
must start running again (an oclp-hook recipe: before it; a post recipe: before the snapshot, since every post runs
after it). A build starts from a saved point at or before that entry: the image as the builder left it just before a
recipe (or before the snapshot), with the volume UUIDs and records it needs (pipeline/stages/golden-image.py). A
point's key is a digest of the base and of every oclp-hook entry before it, so a point is used only when everything
that made it is what the new build would run up to there; anything else is a full build. Every step from the point
on runs as in a full build, in set order, from the same state, which is why the result is the full build's (proved
per change by comparing the two images; DESIGN in the fast-iteration item).
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys

TOP = Path(__file__).resolve().parent.parent
SCHEMA = 1
SNAPSHOT = 'snapshot'                  # the point after every oclp-hook recipe, before the snapshot and the posts
# Assembly keys that decide no byte of the image: where it is committed, the builder's scratch and disk checks, and
# which points to save.
NOT_CONTENT = frozenset({'repo', 'workspace_size_bytes', 'disk_need_gb', 'checkpoints'})
# The kit's small records the base layers or the staging decided (copied into every build record).
BASE_FILES = ('MANIFEST.txt', 'profile.json', 'target-data.json', 'optional.json', 'copy-hooks.py')
# Staging code whose output the kit holds only by row identity (the KDK expanded from its row, the Pythons).
STAGING = ('pipeline/stages/inputs.sh', 'pipeline/stages/weaken-dylibs.py')
# guest-config.json keys that list the set's recipes (compared per recipe instead) or only feed the posts.
CONFIG_RECIPE_KEYS = frozenset({'hooks', 'posts', 'declared'})
SKIP = frozenset({'__pycache__', '.DS_Store'})
CONFIG_ROW = re.compile(r'/inputs/([A-Za-z0-9_.+-]+)$')


class Refused(ValueError):
    """A record or declaration that cannot be planned from."""


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as stream:
        for block in iter(lambda: stream.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def tree_digest(top, skip_top=()):
    """sha256 over TOP's relative names and contents (symbolic links by their text), leaving out caches and the
    top-level names SKIP_TOP. A file TOP is its own content."""
    top = Path(top)
    if top.is_symlink():
        return hashlib.sha256(('link ' + os.readlink(top)).encode()).hexdigest()
    if top.is_file():
        return sha256(top)
    h = hashlib.sha256()
    for folder, dirs, files in os.walk(top):
        rel = os.path.relpath(folder, top)
        links = [d for d in dirs if os.path.islink(os.path.join(folder, d))]
        dirs[:] = sorted(d for d in dirs if d not in SKIP and d not in links and not (rel == '.' and d in skip_top))
        for name in sorted(files + links):
            if name in SKIP or name.endswith('.pyc') or (rel == '.' and name in skip_top):
                continue
            path = os.path.join(folder, name)
            body = 'link ' + os.readlink(path) if os.path.islink(path) else sha256(path)
            h.update(('%s %s\n' % (os.path.normpath(os.path.join(rel, name)), body)).encode())
    return h.hexdigest()


def safe_part(part):
    return (isinstance(part, str) and part and not part.startswith('/') and '\\' not in part and '\0' not in part
            and '..' not in part.split('/') and os.path.normpath(part) == part.rstrip('/'))


def reads_problems(patch):
    """Problems with a patch.json's "input_reads": {ROW: [PART, ...]}, the only parts of input ROW (a folder row) the
    recipe reads; each ROW one of its "inputs", each PART a relative path inside the row (file or folder)."""
    reads = patch.get('input_reads')
    if reads is None:
        return []
    if not isinstance(reads, dict) or not reads:
        return ['input_reads must be an object {ROW: [PART, ...]}']
    bad = []
    for row, parts in sorted(reads.items()):
        if row not in patch.get('inputs', []):
            bad.append('input_reads names %s, which is not one of its inputs' % row)
        if not isinstance(parts, list) or not parts or not all(safe_part(p) for p in parts):
            bad.append('input_reads %s: a list of relative paths inside the row (no "..", no leading /)' % row)
        elif len(set(parts)) != len(parts):
            bad.append('input_reads %s: a part is listed twice' % row)
    return bad


def projection_digest(folder, parts):
    """sha256 over PARTS of the row folder FOLDER, each by its tree_digest ('missing' when absent)."""
    h = hashlib.sha256()
    for part in sorted(parts):
        path = Path(folder)/part
        body = tree_digest(path) if path.exists() or path.is_symlink() else 'missing'
        h.update(('%s %s\n' % (part.rstrip('/'), body)).encode())
    return h.hexdigest()


def input_reads(name, patches=TOP/'patches'):
    """The recipe NAME's "input_reads" ({} without one), refused when malformed."""
    path = Path(patches)/name/'patch.json'
    patch = json.loads(path.read_text()) if path.is_file() else {}
    bad = reads_problems(patch)
    if bad:
        raise Refused('%s: %s' % (name, '; '.join(bad)))
    return patch.get('input_reads', {})


UNPINNED = 'unpinned '


def row_identity(record, top=TOP, store=None):
    """What a recorded input row (golden-run's inputs.json entry: sha256, path) is: its sha256 column; for a '-' row a
    file's own sha256, or a folder of this checkout (tools/edit-engine) by its tree_digest. A '-' folder anywhere else
    (an unpacked tree in the store) is not hashed, which would be slow: it is "unpinned", and an entry that reads one
    always counts as changed."""
    sha, path = record['sha256'], Path(record['path'])
    if sha != '-':
        return sha
    if path.is_file():
        return sha256(path)
    real, repo = path.resolve(), Path(top).resolve()
    stores = [Path(store).resolve()] if store else []
    stores.append((Path(top)/'store').resolve())
    if path.is_dir() and real.is_relative_to(repo) and not any(real.is_relative_to(s) for s in stores):
        return 'tree ' + tree_digest(path)
    return UNPINNED + str(path)


def plan_rows(share):
    """[(stage, name, folder, [row, ...])] of SHARE/patches.txt, folder relative to SHARE (as inputs.sh stages it)."""
    rows = []
    for number, line in enumerate((Path(share)/'patches.txt').read_text().splitlines(), 1):
        parts = line.split()
        if len(parts) < 2:
            continue
        stage, name, inputs = parts[0], parts[1], parts[2:]
        rows.append((stage, name, '%s/%02d-%s' % ('hooks' if stage == 'oclp-hook' else 'post', number, name), inputs))
    return rows


def normalized_config(share, local=None):
    """guest-config.json without the recipe lists, with the build's own LOCAL folder (its workspace mount, which names
    the job) written as {local}; None when the kit has none."""
    path = Path(share)/'guest-config.json'
    if not path.is_file():
        return None
    text = path.read_text()
    if local:
        text = text.replace(json.dumps(str(local))[1:-1], '{local}')
    config = json.loads(text)
    return {k: v for k, v in config.items() if k not in CONFIG_RECIPE_KEYS}


def config_rows(config):
    """Recipe input rows the base layers read through guest-config.json (payload, kernel: .../inputs/ROW)."""
    found = set()

    def walk(value):
        if isinstance(value, dict):
            for v in value.values():
                walk(v)
        elif isinstance(value, list):
            for v in value:
                walk(v)
        elif isinstance(value, str):
            match = CONFIG_ROW.search(value)
            if match:
                found.add(match.group(1))
    walk(config or {})
    return found


def canonical(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def fingerprints(share, input_records, assembly, local=None, store=None, top=TOP, patches=None):
    """The build's fingerprints (module docstring) from its staged kit SHARE, its inputs.json records INPUT_RECORDS
    ({row: {sha256, path}}), its ASSEMBLY (the recipe dict) and the guest's LOCAL folder."""
    share, top = Path(share), Path(top)
    store = Path(store) if store else (top/'store').resolve()
    patches = Path(patches) if patches else top/'patches'
    rows = plan_rows(share)
    recipe_rows = {r for *_, ins in rows for r in ins}
    config = normalized_config(share, local)
    base_rows = (set(input_records) - recipe_rows) | (config_rows(config) & recipe_rows)
    missing = sorted(r for r in base_rows | recipe_rows if r not in input_records)
    if missing:
        raise Refused('no input record for rows %s' % ', '.join(missing))
    identity = {r: row_identity(input_records[r], top, store) for r in sorted(base_rows | recipe_rows)}
    base = dict(assembly={k: v for k, v in sorted(assembly.items()) if k not in NOT_CONTENT},
                rows={r: identity[r] for r in sorted(base_rows)},
                files={n: sha256(share/n) for n in BASE_FILES if (share/n).is_file()},
                source=tree_digest(share/'assembly-source') if (share/'assembly-source').is_dir() else None,
                staging={p: sha256(top/p) for p in STAGING if (top/p).is_file()},
                config=canonical(config) if config is not None else None)
    recipes = []
    for stage, name, folder, ins in rows:
        reads = input_reads(name, patches)
        inputs = {}
        for r in ins:
            if r in reads:
                inputs[r] = 'reads %s' % projection_digest(input_records[r]['path'], reads[r])
            else:
                inputs[r] = identity[r]
        recipes.append(dict(stage=stage, name=name, folder=folder,
                            code=tree_digest(share/folder, skip_top=('inputs',)), inputs=inputs))
    return dict(schema=SCHEMA, base=base, recipes=recipes)


def point_names(fps):
    """The points a build of FPS can start from, in run order: before each oclp-hook recipe, then the snapshot."""
    return [r['name'] for r in fps['recipes'] if r['stage'] == 'oclp-hook'] + [SNAPSHOT]


def point_key(fps, name):
    """The key of the point before NAME (an oclp-hook recipe of FPS, or SNAPSHOT): the base and every oclp-hook
    entry before it. Two builds whose keys agree left the image in the same state there. None when an unpinned row
    (see row_identity) is read before it."""
    names = point_names(fps)
    if name not in names:
        raise Refused('no point %s: not an oclp-hook recipe of the set (points: %s)' % (name, ', '.join(names)))
    hooks = [r for r in fps['recipes'] if r['stage'] == 'oclp-hook']
    before = hooks if name == SNAPSHOT else hooks[:[r['name'] for r in hooks].index(name)]
    identities = list(fps['base']['rows'].values()) + [v for r in before for v in r['inputs'].values()]
    if any(v.startswith(UNPINNED) for v in identities):
        return None     # what an unpinned row holds is not known: no point after it is ever reused
    return canonical(dict(schema=fps['schema'], base=fps['base'], recipes=before))[:24]


def points(fps, assembly):
    """{name: key} of the points the ASSEMBLY's "checkpoints" asks the build to save ({} without it)."""
    names = assembly.get('checkpoints', [])
    if not isinstance(names, list) or not all(isinstance(n, str) for n in names) or len(set(names)) != len(names):
        raise Refused('assembly "checkpoints" must be a list of distinct recipe names or "%s"' % SNAPSHOT)
    return {n: point_key(fps, n) for n in names}


def saved_points(folder):
    """{key: point.json} of the points saved under FOLDER (FOLDER/KEY/point.json beside its target.img)."""
    out = {}
    for entry in sorted(Path(folder).glob('*/point.json')) if Path(folder).is_dir() else []:
        try:
            data = json.loads(entry.read_text())
        except ValueError:
            continue
        if data.get('key') == entry.parent.name and (entry.parent/'target.img').is_file():
            out[data['key']] = data
    return out


def base_differences(old, new):
    out = []
    for field in sorted(set(old) | set(new)):
        a, b = old.get(field), new.get(field)
        if a == b:
            continue
        if isinstance(a, dict) and isinstance(b, dict):
            keys = sorted(k for k in set(a) | set(b) if a.get(k) != b.get(k))
            out.append('%s: %s' % (field, ', '.join(keys)))
        else:
            out.append(field)
    return out


def entry_differences(old, new):
    """Why set entry NEW differs from OLD (the same position in the set), [] when it does not."""
    if old is None:
        return ['added']
    if new is None:
        return ['removed']
    out = [k for k in ('stage', 'name', 'folder', 'code') if old.get(k) != new.get(k)]
    rows = sorted(r for r in set(old['inputs']) | set(new['inputs']) if old['inputs'].get(r) != new['inputs'].get(r))
    unpinned = sorted(r for r, v in new['inputs'].items() if v.startswith(UNPINNED) and r not in rows)
    return out + ['row ' + r for r in rows] + ['row %s unpinned' % r for r in unpinned]


def plan(previous, fps, saved=()):
    """How to make the build of fingerprints FPS given the PREVIOUS build record (build.json as a dict) and the keys
    of the SAVED points: {mode: full | incremental | unchanged, reasons, changed, first_changed, restart, point, key,
    reused, runs}."""
    out = dict(mode='full', reasons=[], changed=[], first_changed=None, restart=None, point=None, key=None,
               reused=[], runs=[])
    old = previous.get('fingerprints')
    if previous.get('status') != 'committed':
        out['reasons'].append('the previous build did not commit an image (status %r)' % previous.get('status'))
    if not old:
        out['reasons'].append('the previous build recorded no fingerprints (it predates incremental builds)')
    elif old.get('schema') != fps['schema']:
        out['reasons'].append('fingerprint schema %r, this builder writes %r' % (old.get('schema'), fps['schema']))
    if previous.get('overlays') or previous.get('recipe', {}).get('overlays'):
        out['reasons'].append('the previous build installed overlays (--from-overlays): not planned incrementally')
    if out['reasons']:
        return out
    differ = base_differences(old['base'], fps['base'])
    differ += ['rows: %s unpinned' % r for r, v in sorted(fps['base']['rows'].items())
               if v.startswith(UNPINNED) and old['base']['rows'].get(r) == v]
    if differ:
        out['reasons'] += ['base: ' + d for d in differ]
        return out
    a, b = old['recipes'], fps['recipes']
    first = None
    for i in range(max(len(a), len(b))):
        why = entry_differences(a[i] if i < len(a) else None, b[i] if i < len(b) else None)
        if why:
            entry = b[i] if i < len(b) else a[i]
            out['changed'].append(dict(name=entry['name'], stage=entry['stage'], why=why))
            if first is None:
                first = i
    if first is None:
        out.update(mode='unchanged', reasons=['every fingerprint matches the previous build: its image is this build'])
        return out
    out['first_changed'] = out['changed'][0]['name']
    hooks = [r['name'] for r in b if r['stage'] == 'oclp-hook']
    entry = b[first] if first < len(b) else None
    restart = entry['name'] if entry and entry['stage'] == 'oclp-hook' else SNAPSHOT
    out['restart'] = restart
    names = point_names(fps)
    saved = set(saved)
    for name in reversed(names[:names.index(restart)+1]):
        key = point_key(fps, name)
        if key is not None and key in saved:
            out.update(mode='incremental', point=name, key=key)
            break
    if out['mode'] == 'full':
        out['reasons'].append('no saved point at or before %s (the first change: %s)' % (restart, out['first_changed']))
        return out
    at = len(hooks) if out['point'] == SNAPSHOT else hooks.index(out['point'])
    out['reused'] = hooks[:at]
    out['runs'] = hooks[at:] + [SNAPSHOT] + [r['name'] for r in b if r['stage'] == 'post']
    out['reasons'].append('first change %s; starts from the saved point before %s' % (out['first_changed'], out['point']))
    return out


def read_record(path):
    """(folder, build.json dict) of a record folder or its build.json."""
    path = Path(path)
    if path.is_dir() and (path/'build.json').is_file():
        path = path/'build.json'
    if path.name != 'build.json' or not path.is_file():
        raise Refused('%s is no build record (a folder with build.json, or the build.json)' % path)
    return path.parent, json.loads(path.read_text())


def previous_record(path, runs=None):
    """(folder, build.json) of PATH: a build record, or an image repository (its image.json beside image.manifest),
    whose newest committed record under RUNS (default store/runs) names it as its repository."""
    path = Path(path)
    if not (path/'image.json').is_file():
        return read_record(path)
    repository = path.resolve()
    runs = Path(runs) if runs else TOP/'store/runs'
    found = []
    for build in runs.glob('*/build.json'):
        try:
            data = json.loads(build.read_text())
        except (OSError, ValueError):
            continue
        if data.get('status') == 'committed' and data.get('repository') and \
                Path(data['repository']).resolve() == repository:
            found.append((build.stat().st_mtime, build.parent.name, build.parent, data))
    if not found:
        raise Refused('no committed build record under %s names the image repository %s' % (runs, repository))
    _, _, folder, data = max(found, key=lambda f: (f[0], f[1]))
    return folder, data


def render(result):
    lines = ['incremental plan: %s' % result['mode']]
    if result['mode'] == 'incremental':
        lines.append('  start from the point before %s (key %s)' % (result['point'], result['key']))
        lines.append('  reused: %s' % (', '.join(result['reused']) or '(no recipe)'))
        lines.append('  runs:   %s' % ', '.join(result['runs']))
    for change in result['changed']:
        lines.append('  changed %s %s: %s' % (change['stage'], change['name'], ', '.join(change['why'])))
    for reason in result['reasons']:
        lines.append('  ' + reason)
    return '\n'.join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(prog='incremental.py', description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('plan', help='how a build of NEW starts from PREVIOUS')
    p.add_argument('previous', type=Path); p.add_argument('new', type=Path)
    p.add_argument('--points', type=Path, default=TOP/'store/cache/golden-points')
    p.add_argument('--runs', type=Path)
    p.add_argument('--json', action='store_true')
    s = sub.add_parser('show', help='a record\'s fingerprints and points')
    s.add_argument('record', type=Path)
    a = parser.parse_args(argv)
    try:
        if a.command == 'show':
            _, data = read_record(a.record)
            print(json.dumps(dict(fingerprints=data.get('fingerprints'), points=data.get('points')), indent=1))
            return 0
        _, previous = previous_record(a.previous, a.runs)
        _, new = read_record(a.new)
        if not new.get('fingerprints'):
            raise Refused('%s recorded no fingerprints' % a.new)
        result = plan(previous, new['fingerprints'], saved_points(a.points))
    except Refused as error:
        print('incremental.py: %s' % error, file=sys.stderr)
        return 2
    print(json.dumps(result, indent=1) if a.json else render(result))
    return 0


if __name__ == '__main__':
    sys.exit(main())
