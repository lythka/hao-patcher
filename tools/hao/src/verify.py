"""hao verify: every installed file of the packs against its manifest, read-only.

A manifest is a pack overlay's manifest.json (pipeline/materialize.py's format, the one hao patch applies); only its
"files" (sha256, mode, owner, flags) and "dirs" are read, so a manifest without its files/ folder will do. They come
from overlay folders, manifest files, or ROOT/private/var/db/hao/manifests (where hao install and build-disk keep the
manifests of what they installed). For each file: missing, different content, or different mode/owner/flags.
"""
import json
import os
from pathlib import Path

import livepatch as LP

INSTALLED = 'private/var/db/hao/manifests'


def data_root(root):
    """The Data volume of an unbooted install whose System volume is mounted at ROOT ("macOS" -> "macOS - Data", as
    the Recovery mounts them), or None. Firmlinked paths (Library, private/var, Users ...) live there; on a booted
    system "/" reaches them through the firmlinks."""
    root = Path(root)
    if str(root) == '/':
        return None
    data = root.parent / (root.name + ' - Data')
    # a real folder next to ROOT, not a link to somewhere else (pin() then fixes where it is)
    return data if data.is_dir() and not data.is_symlink() else None


def manifests(sources, root):
    """[(where, manifest)] from overlay folders or manifest files; ROOT's installed manifests when none are given."""
    paths = []
    if not sources:
        data = data_root(root)
        sources = [d for d in ([Path(root) / INSTALLED] + ([data / INSTALLED] if data else [])) if d.is_dir()] or \
            [Path(root) / INSTALLED]
    for s in sources:
        s = Path(s)
        if s.is_file():
            paths.append(s)
        elif (s / 'manifest.json').is_file():
            paths.append(s / 'manifest.json')
        elif s.is_dir():
            paths += sorted(s.glob('*/manifest.json')) + sorted(s.glob('*.json'))
        else:
            raise LP.PatchError('%s: no manifest there' % s)
    out = []
    for p in paths:
        m = json.loads(p.read_text())
        if m.get('format') != 1 or not isinstance(m.get('files'), dict):
            continue
        out.append((str(p), m))
    if not out:
        raise LP.PatchError('no pack manifests found (give overlay folders, or install with hao first)')
    return out


def inside(root, data, path):
    """The file a manifest PATH names under ROOT (or, for a firmlinked path of an unbooted install, under its Data
    volume DATA), or None when PATH is absolute, has empty, . or .. parts, or resolves outside that root."""
    parts = str(path).split('/')
    if not path or str(path).startswith('/') or any(x in ('', '.', '..') for x in parts):
        return None
    target = root / path
    base = root
    if data is not None and not os.path.lexists(target) and os.path.lexists(data / path):
        target, base = data / path, data
    real_base = os.path.realpath(str(base))
    real_parent = os.path.realpath(str(target.parent))
    if real_parent != real_base and not real_parent.startswith(real_base.rstrip('/') + '/'):
        return None
    return target


def pin(base):
    """Where the kernel says BASE is, fixed once at the start (SAFE.fd_path of its open folder)."""
    import safety as SAFE
    fd = os.open(str(base), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        return SAFE.fd_path(fd).rstrip('/') + '/'
    finally:
        os.close(fd)


def hash_inside(target, base, pinned=None):
    """sha256 of TARGET read through one descriptor (no link followed), or None when it is not a plain file, or
    'outside' when the file that descriptor opened is not under BASE as PINNED at the start (a folder swapped after the
    path was checked, or the root itself swapped later). Without F_GETPATH nothing is hashed ('outside')."""
    import hashlib
    import stat
    import safety as SAFE
    try:
        fd = os.open(str(target), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return None
        try:
            where = SAFE.fd_path(fd)
            real = pinned or pin(base)
        except OSError:
            return 'outside'
        if not where.startswith(real):
            return 'outside'
        h = hashlib.sha256()
        while True:
            block = os.read(fd, 1 << 20)
            if not block:
                break
            h.update(block)
        return h.hexdigest()
    finally:
        os.close(fd)


def verify(root, found, attributes=None, on_file=None):
    """{'ok': n, 'problems': [{path, pack, problem, ...}], 'packs': {pack: files}, 'changed': [rows]}; ON_FILE(row) per
    file. A manifest entry with "content": "seed" (a starting value macOS rewrites after install, such as a Metal shader
    cache; "reason" says why) is checked for presence, place, type, owner and flags but not its sha256, size or mode: a
    changed seed is a row with changed="seed" and its reason, counted as ok, never a problem."""
    root = Path(root)
    data = data_root(root)
    attributes = attributes or LP.Attributes(root, privileged=True)
    out = {'ok': 0, 'problems': [], 'packs': {}, 'changed': []}
    pins = {root: pin(root)}
    if data is not None:
        pins[data] = pin(data)
    for where, m in found:
        pack = m.get('pack', Path(where).parent.name)
        out['packs'][pack] = len(m['files'])
        for path, meta in sorted(m['files'].items()):
            row = {'path': path, 'pack': pack}
            target = inside(root, data, path)
            if target is None:
                # a manifest path that is absolute, has . or .. parts, or leads out of the root through a symlinked
                # folder: refused, never read or hashed (a manifest must not point hao at other files)
                row['problem'] = 'outside'
                out['problems'].append(row)
                if on_file:
                    on_file(row)
                continue
            base = data if data is not None and target.is_relative_to(data) else root
            have = hash_inside(target, base, pins[base])
            if have == 'outside':
                row['problem'] = 'outside'
            elif have is None:
                row['problem'] = 'missing'
            else:
                seed = meta.get('content') == 'seed'
                if have != meta['sha256'] and not seed:
                    row.update(problem='content', want=meta['sha256'], have=have)
                else:
                    if have != meta['sha256']:
                        row.update(changed='seed', reason=str(meta.get('reason') or 'a seed macOS rewrites after install'))
                    try:
                        keys = sum(attributes.differs(target, attributes.wanted(meta)), [])
                        if seed:
                            # macOS rewrites a seed with its writer's umask (root's Metal cache comes back 0600 at
                            # first boot, bytes unchanged): its mode is macOS's, its owner and flags still ours
                            keys = [k for k in keys if k != 'mode']
                    except LP.PatchError as x:
                        keys = ['owner (%s)' % x]
                    if keys:
                        row.update(problem='attributes', keys=keys)
            if 'problem' in row:
                out['problems'].append(row)
            else:
                out['ok'] += 1
                if row.get('changed'):
                    out['changed'].append(row)
            if on_file:
                on_file(row)
    return out
