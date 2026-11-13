#!/usr/bin/env python3
"""Install one recipe's files from the materialised overlays, in place of its hook (golden-run --from-overlays).

Usage (in the builder VM, by golden-image.py, at the recipe's turn in set order):
  python3 -I golden-overlays.py install PLAN FILES --recipe NAME --livepatch LIVEPATCH.py
          --root system=MOUNT [--root data=MOUNT] [--root esp=MOUNT] --record OUT.json

PLAN is the host's pipeline/overlays.py schedule() (SHARE/overlay-plan.json), FILES the staged files
(FILES/<path>). For every file of the recipe's step: the staged copy must have the plan's sha256; when every writer
of the file comes from the overlays the file there now must be the one the materialiser started from (an edit's
input: refused if not; a replaced file: recorded); then it is written as tools/hao writes a file (a new file
renamed over the old one, the old one's flags cleared first), folders it creates get the manifest's "dirs"
attributes, and mode, owner and flags are the manifest's, the mtime too when the manifest dates the file (a tar
member). Before the files, every folder of the step's "dirs" that is missing (all the folders the recipe's tar holds,
empty ones included, as its `tar -x` creates them) is made, parents first; once the files are in, each folder made
gets the mtime "dirs" dates it with. The result must hash to the plan's sha256. The EFI partition (FAT) takes only the bytes. A Data path is
checked against the image's own firmlinks before it is written there.

Nothing here runs on the host: golden-image.py calls it inside the builder VM only.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def load_livepatch(path):
    spec = importlib.util.spec_from_file_location('livepatch', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def image_firmlinks(system):
    f = Path(system, 'usr/share/firmlinks')
    if not f.is_file():
        return None
    return sorted({line.split('\t')[0].strip('/') for line in f.read_text().splitlines() if line.strip()})


def volume_check(path, volume, links):
    """Refuse a Data path the image's firmlinks don't put on Data (the host planned with other firmlinks)."""
    if volume != 'data':
        return
    if links is None:
        raise ValueError('%s: planned on Data, but the image has no usr/share/firmlinks' % path)
    if not any(path == f or path.startswith(f + '/') for f in links):
        raise ValueError('%s: planned on Data, but no firmlink of this image covers it' % path)


def install(plan, files, recipe, roots, livepatch, attributes=None):
    """Install RECIPE's step of PLAN from FILES into ROOTS ({volume: folder}); returns the record."""
    step = next((s for s in plan['steps'] if s['name'] == recipe), None)
    if step is None or step['mode'] != 'overlay':
        raise ValueError('%s is not installed from the overlays in this plan' % recipe)
    lookup = roots.get('data') or roots['system']
    attributes = attributes or livepatch.Attributes(lookup)   # owners by name: the image's own passwd/group
    links = image_firmlinks(roots['system'])
    record = {'recipe': recipe, 'files': [], 'warnings': [], 'folders': []}
    dirs = step.get('dirs', {})
    made = {}                                                # volume -> folders created on it
    for d, meta in sorted(dirs.items()):                     # parents first: every folder its tar holds, empty or not
        volume = meta.get('volume')
        if volume in (None, 'esp'):                          # a plan without volumes: folders above files only
            continue
        if volume not in roots:
            raise ValueError('%s: no %s volume given' % (d, volume))
        volume_check(d, volume, links)
        created = made.setdefault(volume, [])
        livepatch._folders(lambda rel, r=Path(roots[volume]): r / rel, d, dirs, attributes, created, itself=True)
    for f in step['files']:
        path, volume = f['path'], f['volume']
        if volume not in roots:
            raise ValueError('%s: no %s volume given' % (path, volume))
        volume_check(path, volume, links)
        root = Path(roots[volume])
        source = Path(files, path)
        if sha256_file(source) != f['sha256']:
            raise ValueError('%s: the staged file does not match the plan\'s sha256' % path)
        target = root / path
        base = f.get('base')
        if base:
            at = root / base['path']
            have = sha256_file(at) if at.is_file() and not at.is_symlink() else None
            if have != base['sha256']:
                message = '%s is %s here; the overlay was made from %s' % (base['path'], have, base['sha256'])
                if base.get('edited'):
                    raise ValueError(message + ' (an edit of that file: refusing)')
                record['warnings'].append(message + ' (replaced whole)')
        before = sha256_file(target) if target.is_file() and not target.is_symlink() else None
        if volume == 'esp':                                  # FAT: no owners, modes or flags
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            not_set = ['mode', 'owner', 'flags']
        else:
            livepatch._folders(lambda rel: root / rel, path, dirs, attributes, made.setdefault(volume, []))
            not_set = livepatch._install(source, target, attributes.wanted(f, '0644'), attributes)
            if 'mtime' in f:
                os.utime(target, (f['mtime'], f['mtime']), follow_symlinks=False)
        after = sha256_file(target)
        if after != f['sha256']:
            raise ValueError('%s: wrote %s, expected %s' % (path, after, f['sha256']))
        record['files'].append({'path': path, 'volume': volume, 'before': before, 'after': after,
                                'not_set': not_set})
    for volume, created in sorted(made.items()):         # a tar folder's mtime, once its files are in (as tar -x)
        livepatch._folder_times(lambda rel, r=Path(roots[volume]): r / rel, created, dirs)
        record['folders'] += created
    return record


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest='command', required=True)
    i = sub.add_parser('install')
    i.add_argument('plan', type=Path); i.add_argument('files', type=Path)
    i.add_argument('--recipe', required=True); i.add_argument('--livepatch', type=Path, required=True)
    i.add_argument('--root', action='append', default=[], metavar='VOLUME=MOUNT')
    i.add_argument('--record', type=Path, required=True)
    a = p.parse_args(argv)
    roots = dict(r.split('=', 1) for r in a.root)
    if 'system' not in roots:
        p.error('--root system=MOUNT is required')
    record = install(json.loads(a.plan.read_text()), a.files, a.recipe, roots, load_livepatch(a.livepatch))
    a.record.parent.mkdir(parents=True, exist_ok=True)
    a.record.write_text(json.dumps(record, indent=1) + '\n')
    for w in record['warnings']:
        print('golden-overlays: warning:', w, flush=True)
    print('golden-overlays: %s: %d files from the overlays, %d folders made' % (
        a.recipe, len(record['files']), len(record['folders'])), flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
