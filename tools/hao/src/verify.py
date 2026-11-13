"""hao verify: every installed file of the packs against its manifest, read-only.

A manifest is a pack overlay's manifest.json (pipeline/materialize.py's format, the one hao patch applies); only its
"files" (sha256, mode, owner, flags) and "dirs" are read, so a manifest without its files/ folder will do. They come
from overlay folders, manifest files, or ROOT/private/var/db/hao/manifests (where hao install and build-disk keep the
manifests of what they installed). For each file: missing, different content, or different mode/owner/flags.
"""
import json
from pathlib import Path

import livepatch as LP

INSTALLED = 'private/var/db/hao/manifests'


def manifests(sources, root):
    """[(where, manifest)] from overlay folders or manifest files; ROOT's installed manifests when none are given."""
    paths = []
    for s in sources or [Path(root) / INSTALLED]:
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


def verify(root, found, attributes=None, on_file=None):
    """{'ok': n, 'problems': [{path, pack, problem, ...}], 'packs': {pack: files}}; ON_FILE(row) per file."""
    root = Path(root)
    attributes = attributes or LP.Attributes(root, privileged=True)
    out = {'ok': 0, 'problems': [], 'packs': {}}
    for where, m in found:
        pack = m.get('pack', Path(where).parent.name)
        out['packs'][pack] = len(m['files'])
        for path, meta in sorted(m['files'].items()):
            target = root / path
            row = {'path': path, 'pack': pack}
            if target.is_symlink() or not target.is_file():
                row['problem'] = 'missing'
            else:
                have = LP.sha256_file(target)
                if have != meta['sha256']:
                    row.update(problem='content', want=meta['sha256'], have=have)
                else:
                    try:
                        keys = sum(attributes.differs(target, attributes.wanted(meta)), [])
                    except LP.PatchError as x:
                        keys = ['owner (%s)' % x]
                    if keys:
                        row.update(problem='attributes', keys=keys)
            if 'problem' in row:
                out['problems'].append(row)
            else:
                out['ok'] += 1
            if on_file:
                on_file(row)
    return out
