"""The assets folder: everything an image build reads, laid out as the build's store (each input row's path below
`store/`, plus the builder's own tools), so a fresh clone builds from it: `hao build-image REPO --assets DIR` links
the clone's `store` to DIR, checks every row and builds.

    make   (development) fill a new folder from our own store with APFS clones: the stand-in for `hao fetch` plus the
           producers that make each Apple-derived row on the user's Mac, until those run from the downloads
    check  every row the assembly reads is there and has its pinned hash; the builder's tools are there

Rows are found as release-cut finds them (the set's recipes and the assembly's donor rows) plus the rows golden-run
always reads (KDK, both CPythons, site packages, the firmlink tool, OpenCore, PatcherSupportPkg).
"""
import json
import os
from pathlib import Path
import runpy
import shutil
import subprocess

TOP = Path(__file__).resolve().parents[3]
# golden-run's own inputs besides the set's (its used_rows); the PatcherSupportPkg row follows from the OCLP commit
FIXED = ('kdk', 'python_arm64', 'python_x86_64', 'python_site', 'fl')
# what the builder needs below store/ that no input row names: OCLP's source repository (git archive at the
# assembly's commit) and the builder VM (base image, tools, vmctl's Python)
TOOLS = ('oclp', 'vm/base', 'vm/bin', 'cache/vmctl-venv')
# a Mac that builds without the VM (an Intel Mac) needs none of the VM, and no git: OCLP's source comes as an archive
HERE_TOOLS = ('oclp-source',)


def _lock():
    return runpy.run_path(str(TOP / 'pipeline' / 'lock.py'))


def oclp_psp(store, commit):
    """The PatcherSupportPkg version OCLP at COMMIT expects (lock.py psp): from the source archive the assets folder
    carries (oclp-source/COMMIT.tar), else from the repository at that commit."""
    import re
    import tarfile
    tar = Path(store) / 'oclp-source' / (commit + '.tar')
    if tar.is_file():
        with tarfile.open(tar) as t:
            text = t.extractfile('opencore_legacy_patcher/constants.py').read().decode()
    else:
        text = subprocess.run(['git', '-C', str(Path(store) / 'oclp'), 'show',
                               commit + ':opencore_legacy_patcher/constants.py'],
                              capture_output=True, text=True, check=True).stdout
    return re.search(r'patcher_support_pkg_version:\s*str\s*=\s*"([^"]+)"', text).group(1)


def rows(assembly_path, store):
    """[(row, lock folder, sha256 or '-', path relative to the repository)] for every input the build reads."""
    cut = runpy.run_path(str(TOP / 'pipeline' / 'release-cut'))
    a = json.loads(Path(assembly_path).read_text())
    folders = [TOP / 'pipeline/inputs/golden' / a['golden_build'], TOP / 'pipeline/inputs' / a['donor_build'],
               TOP / 'pipeline/inputs/common']
    lock = _lock()
    tables = [(f, lock['rows'](str(f))) for f in folders if f.is_dir()]
    names = [r for r, _ in cut['rows_for'](assembly_path)]
    names += list(FIXED) + ['opencore_' + a.get('opencore', 'release')]
    if (Path(store) / 'oclp').exists():
        names.append('psp_' + oclp_psp(store, a['oclp']))
    out, seen = [], set()
    for name in names:
        if name in seen:
            continue
        seen.add(name)
        hit = next(((f, t[name]) for f, t in tables if name in t), None)
        if hit is None:
            out.append((name, None, None, None))
            continue
        folder, (sha, path) = hit
        out.append((name, folder, sha, os.path.relpath(path, TOP)))
    return out


def in_store(relative):
    """The path below store/ of a row path, or None for a row that is a tracked file of the checkout."""
    parts = Path(relative).parts
    return Path(*parts[1:]) if parts and parts[0] == 'store' and len(parts) > 1 else None


def check(assembly_path, store, here=False):
    """Problems, one line each: a row that is missing or has another hash, a builder tool that is missing. HERE: the
    build runs on this Mac without the builder VM, so it needs OCLP's source archive instead of the VM."""
    lock = _lock()
    problems = []
    for name, folder, sha, relative in rows(assembly_path, store):
        if folder is None:
            problems.append('%s: no input row names it' % name)
            continue
        below = in_store(relative)
        if below is None:
            # a tracked file of this checkout (the commit pins it): lock.py checks it where it is
            try:
                lock['path'](str(folder), name)
            except SystemExit as e:
                problems.append('%s: %s' % (name, e))
            continue
        # every other row is checked inside STORE, the folder named, not this checkout's store/ (a fresh clone
        # has none until build-image --assets links it)
        where = Path(store) / below
        if not where.exists():
            problems.append('%s: missing (%s)' % (name, below))
        elif sha != '-' and lock['row_sha256'](str(where)) != sha:
            problems.append('%s: %s is not %s' % (name, below, sha))
    if here:
        a = json.loads(Path(assembly_path).read_text())
        tar = Path(store) / 'oclp-source' / (a['oclp'] + '.tar')
        if not tar.is_file() or not tar.with_suffix('.commit').is_file():
            problems.append('builder: missing oclp-source/%s.tar (and .commit)' % a['oclp'])
    else:
        for tool in TOOLS:
            if not (Path(store) / tool).exists():
                problems.append('builder: missing %s' % tool)
    return problems


def oclp_archive(store, commit, dest):
    """oclp-source/COMMIT.tar (git archive of OCLP at COMMIT) and COMMIT.commit (the full commit) in DEST, made from
    STORE's OCLP repository: what a Mac without git builds from."""
    out = Path(dest) / 'oclp-source'
    out.mkdir(parents=True, exist_ok=True)
    repo = str(Path(store) / 'oclp')
    with (out / (commit + '.tar')).open('wb') as f:
        subprocess.run(['git', '-C', repo, 'archive', '--format=tar', commit], stdout=f, check=True)
    full = subprocess.run(['git', '-C', repo, 'rev-parse', commit], capture_output=True, text=True, check=True)
    (out / (commit + '.commit')).write_text(full.stdout.strip() + '\n')
    return 'oclp-source/%s.tar' % commit


def clone(src, dest):
    """An APFS clone of SRC (file or folder, links kept) at DEST; a plain copy where cloning is not possible."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if subprocess.run(['cp', '-c', '-R', '-p', str(src), str(dest)]).returncode:
        if src.is_dir():
            shutil.copytree(src, dest, symlinks=True)
        else:
            shutil.copy2(src, dest)


def make(dest, assembly_path, store):
    """Fill the new folder DEST from STORE: every row the assembly's build reads, at its path below store/, and the
    builder's tools. Returns [(what, path below DEST)]."""
    dest = Path(dest)
    if dest.exists() and any(dest.iterdir()):
        raise ValueError('%s is not empty' % dest)
    dest.mkdir(parents=True, exist_ok=True)
    made = []
    for name, folder, sha, relative in rows(assembly_path, store):
        below = in_store(relative) if relative else None
        if below is None or (dest / below).exists():
            continue
        src = Path(store) / below
        if not src.exists():
            raise ValueError('%s: %s is not in %s' % (name, below, store))
        clone(src, dest / below)
        made.append((name, str(below)))
    for tool in TOOLS:
        if (Path(store) / tool).exists() and not (dest / tool).exists():
            clone(Path(store) / tool, dest / tool)
            made.append(('builder', tool))
    repo = Path(store) / 'oclp'
    if (repo / '.git').exists() or (repo / 'HEAD').is_file():   # a git repository (a work tree or a bare one)
        made.append(('builder', oclp_archive(store, json.loads(Path(assembly_path).read_text())['oclp'], dest)))
    return made
