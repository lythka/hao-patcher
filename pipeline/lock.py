#!/usr/bin/python3 -I
"""lock.py: read inputs locks and target files (plain data) for the pipeline's shell stages.

  lock.py path LOCK NAME          the row's path; a row with a sha256 is checked against the file or folder
  lock.py hash PATH               a row's sha256 for PATH: a file's sha256, or a folder's content_sha256
  lock.py seal LOCK NAME          give the '- PATH' row NAME in folder LOCK the sha256 of what PATH holds now
                                  (LOCK may be a target .json: NAME is then a key holding {"path", "sha256"})
  lock.py target TARGET KEY       a value from targets/<model>.json (dotted KEY; objects print as JSON)
  lock.py kdk-name DMG            Kernel_Debug_Kit_26.7.2_build_25G330.dmg -> KDK_26.7.2_25G330.kdk
  lock.py psp OCLP_SOURCE         the PatcherSupportPkg version an OCLP source tree expects
  lock.py key STORE LOCKS ITEM... a 12-hex cache key over ITEMs (pipeline/run's pristine and oclp stage keys);
                                  LOCKS = lock folders, ':'-separated, searched in order for a row
  lock.py key-lines STORE LOCKS ITEM...   the lines that key hashes (to see why a key changed)
  lock.py oclp-keys STORE LOCKS PATCHES DIR ITEM... < HOOKS
                                  the oclp stage's cache keys (pipeline/run): HOOKS = the set's oclp-hook plan lines,
                                  ITEM... = the stage's own items, DIR = a point's cache folder with {key} in it.
                                  Prints `full KEY`, `before NAME KEY DIR` per kc-hook NAME, and `resume NAME` for the
                                  last point whose DIR/target.img exists (oclp_points)

A key item is row:NAME, json:TARGET.json, file:PATH, patch:PATCH_DIR or text:STRING (see key_line). A key is made of
what the inputs are, never of where the store is or of a row's comments: a row counts by its sha256 column, so moving
the store or editing a comment keeps cache hits and any hashed content change misses.

A lock is a folder with one file per row (pipeline/inputs/<build>/<NAME>, pipeline/inputs/common/<NAME>), so rows
can be added independently. The file holds `SHA256-or-- PATH [# comment]`; lines starting with # are comments,
except `# check: KIND key=value ...` (see checks: a folder row checked by its own manifest). A
single file of `NAME SHA256-or-- PATH` rows is also read (tests, one-off locks). PATH may start with ~; a relative
PATH (`store/...`, `patches/...`) is relative to the repository root.
"""
import hashlib, json, os, re, stat, sys

TOP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def resolve(p):
    """A row path: ~ expanded, a relative path taken from the repository root."""
    p = os.path.expanduser(p)
    return p if os.path.isabs(p) else os.path.join(TOP, p)


def rows(lock):
    out = {}
    if os.path.isdir(lock):
        for name in sorted(os.listdir(lock)):
            if name.startswith('.'):
                continue
            data = [l.split('#', 1)[0].split() for l in open(os.path.join(lock, name)) if not l.lstrip().startswith('#')]
            data = [d for d in data if d]
            if len(data) != 1 or len(data[0]) != 2:
                sys.exit('%s/%s: want one line SHA256|- PATH' % (lock, name))
            out[name] = (data[0][0], resolve(data[0][1]))
        return out
    for line in open(lock):
        line = line.split('#', 1)[0].split() if not line.lstrip().startswith('#') else []
        if line:
            if len(line) != 3:
                sys.exit('%s: bad row %r (want NAME SHA256|- PATH)' % (lock, ' '.join(line)))
            if line[0] in out:
                sys.exit('%s: row %s twice' % (lock, line[0]))
            out[line[0]] = (line[1], resolve(line[2]))
    return out


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for b in iter(lambda: f.read(1 << 22), b''):
            h.update(b)
    return h.hexdigest()


CHECK = re.compile(r'^#\s*check:\s*(\S+)((?:\s+[a-z_0-9]+=\S+)*)\s*$')


def checks(lock, name):
    """A folder row's `# check: KIND key=value ...` lines (a lock folder's row file only), as [(KIND, {key: value})].
    `# check: payload os_build=B layout_version=N [manifest_sha256=H]` makes the row a hao-runtime payload checked by
    the hao-contract checker in tools/hao-contract (payload-manifest.json: clean, OS build, install layout, every file's sha256)."""
    f = os.path.join(lock, name)
    if not os.path.isdir(lock) or not os.path.isfile(f):
        return []
    out = []
    for line in open(f):
        if re.match(r'^#\s*check:', line):
            m = CHECK.match(line.rstrip('\n'))
            if not m:
                sys.exit('%s: bad check line %r (want # check: KIND key=value ...)' % (f, line.strip()))
            out.append((m.group(1), dict(kv.split('=', 1) for kv in m.group(2).split())))
    return out


def check_row(lock, name, p):
    """Problems with row NAME at P by its check lines ([] when it has none or all pass)."""
    bad = []
    for kind, kv in checks(lock, name):
        if kind != 'payload':
            bad.append('%s: unknown check %s' % (name, kind))
            continue
        missing = {'os_build', 'layout_version'} - set(kv)
        if missing:
            bad.append('%s: check payload needs %s' % (name, ', '.join(sorted(missing))))
            continue
        manifest = os.path.join(p, 'payload-manifest.json')
        if 'manifest_sha256' in kv and os.path.isfile(manifest) and sha256(manifest) != kv['manifest_sha256']:
            bad.append('%s: %s is not %s' % (name, manifest, kv['manifest_sha256']))
            continue
        import runpy   # the vendored checker, as pipeline/tests/test_contract.py loads it
        contract = runpy.run_path(os.path.join(TOP, 'tools', 'hao-contract', 'hao_contract.py'))
        bad += ['%s: %s' % (name, b) for b in contract['check_payload'](
            TOP, {'manifest': manifest, 'os_build': kv['os_build'], 'layout_version': int(kv['layout_version'])})]
    return bad


def content_sha256(top):
    """sha256 over a folder: one line per entry in sorted walk order (relative name, permission bits, and the file's
    sha256, the link's target text, or 'dir'). Ownership and times are left out: the builder stages as root."""
    h = hashlib.sha256()
    for d, dirs, files in os.walk(top):
        dirs.sort()
        for f in sorted(files + dirs):
            p = os.path.join(d, f)
            st = os.lstat(p)
            if stat.S_ISLNK(st.st_mode):
                body = 'link ' + os.readlink(p)
            elif stat.S_ISDIR(st.st_mode):
                body = 'dir'
            elif stat.S_ISREG(st.st_mode):
                body = 'file ' + sha256(p)
            else:
                sys.exit('%s: not a file, folder or symlink' % p)
            h.update(('%s %04o %s\n' % (os.path.relpath(p, top), stat.S_IMODE(st.st_mode), body)).encode())
    return h.hexdigest()


def row_sha256(p):
    """What a row's sha256 column says about PATH: the file's sha256 or the folder's content_sha256."""
    if os.path.isdir(p):
        return content_sha256(p)
    return sha256(p)


def in_checkout(p):
    """A tracked path of this checkout (pinned by the commit), not something in the store."""
    rp = os.path.realpath(p)
    top = os.path.realpath(TOP)
    store = os.path.realpath(os.path.join(TOP, 'store'))
    return rp.startswith(top + os.sep) and not rp.startswith(store + os.sep)


def seal(lock, name):
    """Write the sha256 of what a '- PATH' row's PATH holds now into the row (comments kept)."""
    f = os.path.join(lock, name)
    want, p = rows(lock)[name]
    if want != '-':
        sys.exit('%s already has a sha256' % f)
    if not os.path.exists(p):
        sys.exit('%s: %s is not there' % (f, p))
    h = row_sha256(p)
    lines = open(f).read().splitlines()
    i = next(i for i, l in enumerate(lines) if l.split() and not l.lstrip().startswith('#'))
    lines[i] = h + lines[i].lstrip()[1:]
    open(f, 'w').write('\n'.join(lines) + '\n')
    return '%s %s' % (h, name)


def path(lock, name):
    r = rows(lock) if not lock.endswith('.json') else \
        {k: (v['sha256'], resolve(v['path'])) for k, v in json.load(open(lock)).items() if isinstance(v, dict) and 'path' in v}
    if name not in r:
        sys.exit('%s has no row %s' % (lock, name))
    want, p = r[name]
    if want != '-' and not os.path.exists(p):
        sys.exit('%s: %s is not there (%s)' % (lock, p, name))
    if want != '-' and row_sha256(p) != want:
        sys.exit('%s: %s is not %s (%s)' % (lock, p, want, name))
    bad = check_row(lock, name, p)
    if bad:
        sys.exit('\n'.join('%s: %s' % (lock, b) for b in bad))
    return p


def row_identity(sha, p, store):
    """What a row's input is, independent of the store's location: its sha256 column; for a '-' row the file's own
    content digest, else (a folder, or not there yet) its path relative to the store (outside the store: the path).
    A '-' folder (a whole unpacked macOS tree) is named by its path rather than hashed, which would be slow."""
    if sha != '-':
        return sha
    if os.path.isfile(p):
        return sha256(p)
    rp, rs = os.path.realpath(p), os.path.realpath(store)
    return 'store/' + os.path.relpath(rp, rs) if rp.startswith(rs + os.sep) else rp


def canonical_target(v):
    """A target file's data without row paths and notes: an object with a sha256 counts by that hash."""
    if isinstance(v, dict):
        if 'path' in v and isinstance(v.get('sha256'), str) and v['sha256'] != '-':
            v = {k: x for k, x in v.items() if k not in ('path', 'note')}
        return {k: canonical_target(x) for k, x in v.items()}
    if isinstance(v, list):
        return [canonical_target(x) for x in v]
    return v


def tree_digest(top):
    """sha256 over a folder's relative names and file contents (symlinks by their target text)."""
    h = hashlib.sha256()
    for d, dirs, files in os.walk(top):
        dirs.sort()
        for f in sorted(files + [x for x in dirs if os.path.islink(os.path.join(d, x))]):
            p = os.path.join(d, f)
            body = 'link ' + os.readlink(p) if os.path.islink(p) else sha256(p)
            h.update(('%s %s\n' % (os.path.relpath(p, top), body)).encode())
    return h.hexdigest()


def key_line(item, store, locks):
    kind, _, arg = item.partition(':')
    if kind == 'row':
        for lock in locks:
            if os.path.isfile(os.path.join(lock, arg)):
                sha, p = rows(lock)[arg]
                return 'row %s %s' % (arg, row_identity(sha, p, store))
        sys.exit('key: no row %s in %s' % (arg, ' '.join(locks)))
    if kind == 'json':
        data = canonical_target(json.load(open(arg)))
        return 'json %s %s' % (os.path.basename(arg), hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest())
    if kind == 'file':
        return 'file %s %s' % (os.path.basename(arg), sha256(arg))
    if kind == 'patch':
        rev = json.load(open(os.path.join(arg, 'patch.json'))).get('revision')
        return 'patch %s r%s %s' % (os.path.basename(arg.rstrip('/')), rev, tree_digest(os.path.join(arg, 'src')))
    if kind == 'text':
        return 'text ' + ' '.join(arg.split())
    sys.exit('key: bad item %r (want row:, json:, file:, patch: or text:)' % item)


def key_lines(store, locks, *items):
    return '\n'.join(key_line(i, store, [l for l in locks.split(':') if l]) for i in items)


def key(store, locks, *items):
    return hashlib.sha256((key_lines(store, locks, *items) + '\n').encode()).hexdigest()[:12]


def patch_directory(name, root):
    return os.path.join(root, name)


def oclp_points(hooks, patches):
    """The oclp stage's cache points for the set's oclp-hook plan lines (`oclp-hook NAME ROW...`, set order), as
    [(NAME, applied kc-hooks, plan lines)]: (None, all kc-hooks, every line) for the whole stage (after the snapshot),
    then one point before each kc-hook NAME. oclp.sh runs OCLP (with every hook.sh) in one builder session and each
    kc-hook in a session of its own, so the state before kc-hook NAME is the target after OCLP and the kc-hooks
    before NAME: its lines leave out only the kc-hook-only patches from NAME on (a patch with a hook.sh counts in every
    point, OCLP ran it). Two sets that reach the same state share the point (no NAME in it)."""
    def has(line, f):
        return os.path.isfile(os.path.join(patch_directory(line.split()[1], patches), 'src', f))
    kc = [l for l in hooks if has(l, 'kc-hook.sh')]
    points = [(None, [l.split()[1] for l in kc], list(hooks))]
    for i, line in enumerate(kc):
        points.append((line.split()[1], [l.split()[1] for l in kc[:i]],
                       [l for l in hooks if l not in kc[i:] or has(l, 'hook.sh')]))
    return points


def oclp_keys(store, locks, patches, template, *base, hooks=None):
    if hooks is None:
        hooks = sys.stdin.read()
    hooks = [' '.join(l.split()) for l in hooks.splitlines() if l.strip()]
    out, resume = [], None
    for name, applied, lines in oclp_points(hooks, patches):
        items = list(base)
        if name:
            items.append('text:point after kc-hooks ' + ' '.join(applied))
        items.append('text:' + '\n'.join(lines))
        for l in lines:
            l = l.split()
            items += ['patch:' + patch_directory(l[1], patches)] + ['row:' + r for r in l[2:]]
        k = key(store, locks, *items)
        if not name:
            out.append('full ' + k)
            continue
        d = template.replace('{key}', k)
        out.append('before %s %s %s' % (name, k, d))
        if os.path.isfile(os.path.join(d, 'target.img')):
            resume = name
    if resume:
        out.append('resume ' + resume)
    return '\n'.join(out)


def target(file, key):
    v = json.load(open(file))
    for k in key.split('.'):
        v = v[k]
    if isinstance(v, str):
        return os.path.expanduser(v) if v.startswith('~/') else v
    return json.dumps(v, indent=1)


def kdk_name(dmg):
    m = re.fullmatch(r'Kernel_Debug_Kit_([\d.]+)_build_(\w+)\.dmg', os.path.basename(dmg))
    if not m:
        sys.exit('not a KDK dmg name: ' + dmg)
    return 'KDK_%s_%s.kdk' % m.groups()


def psp(src):
    text = open(os.path.join(src, 'opencore_legacy_patcher/constants.py')).read()
    return re.search(r'patcher_support_pkg_version:\s*str\s*=\s*"([^"]+)"', text).group(1)


if __name__ == '__main__':
    cmd, args = sys.argv[1], sys.argv[2:]
    print({'path': path, 'hash': row_sha256, 'seal': seal, 'target': target, 'kdk-name': kdk_name, 'psp': psp, 'key': key, 'key-lines': key_lines,
           'oclp-keys': oclp_keys}[cmd](*args))
