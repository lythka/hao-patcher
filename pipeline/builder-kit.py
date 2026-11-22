#!/usr/bin/env python3.14
"""Deterministic builder source snapshot, read exclusively from a Git commit."""
import gzip
import io
import json
from pathlib import Path
import subprocess
import tarfile

ROOTS = ('pipeline/', 'packs/', 'patches/', 'tools/hao/', 'tools/acquire/',
         'tools/hao-contract/', 'tools/edit-engine/', 'tools/kernel-foundation/',
         'tools/tahoe-donors/', 'tools/oclp-hardware/', 'tools/oclp-integration/',
         'tools/kc-binder/', 'tools/live-text/', 'tools/shared-cache/',
         'tools/first-boot-seeds/', 'tools/cache-dylib/', 'tools/gg-recovery/', 'cache/metallib/')
ROOTS += ('tools/audiomxd-fix/', 'tools/metal-fixes/')
FILES = ('LICENSE', 'NOTICE.md', 'tools/kernel-fixes/profiles/26B131/kc-recipe.json')


def selected(name):
    parts = Path(name).parts
    if not name.startswith(ROOTS) and name not in FILES:
        return False
    if any(p in ('tests', '__pycache__', '.DS_Store') for p in parts):
        return False
    if name == 'cache/metallib/shader-runtime-helpers/assemble.c':
        return True  # runtime-build records its prebuilt assembler's source hash
    if name.endswith(('.c', '.h', '.cpp', '.m', '.mm', '.swift', '.o', '.S')):
        return False
    return True


def build(top, commit, output, extras=None):
    """Write a normalized gzip/tar; neither ignored nor working-tree bytes enter it."""
    commit = subprocess.check_output(['git', '-C', str(top), 'rev-parse', commit + '^{commit}'], text=True).strip()
    raw = subprocess.check_output(['git', '-C', str(top), 'archive', '--format=tar', commit])
    entries = {}
    with tarfile.open(fileobj=io.BytesIO(raw)) as source:
        for member in source:
            if not selected(member.name) or member.isdir():
                continue
            if not member.isfile():
                raise ValueError('builder kit requires regular source files: ' + member.name)
            entries[member.name] = (source.extractfile(member).read(), 0o755 if member.mode & 0o111 else 0o644)
    for name, data in (extras or {}).items():
        entries[name] = (data, 0o644)
    entries['pipeline/SOURCE_COMMIT'] = ((commit + '\n').encode(), 0o644)
    entries['.hao-source'] = ((json.dumps({'commit': commit, 'dirty': False}, sort_keys=True) + '\n').encode(), 0o644)
    with Path(output).open('wb') as stream, gzip.GzipFile(filename='', fileobj=stream, mode='wb', mtime=0) as compressed:
        with tarfile.open(fileobj=compressed, mode='w', format=tarfile.USTAR_FORMAT) as archive:
            for name, (data, mode) in sorted(entries.items()):
                info = tarfile.TarInfo(name)
                info.size, info.mode, info.mtime = len(data), mode, 0
                archive.addfile(info, io.BytesIO(data))
    return sorted(entries)


def release_assets(top, release, stage):
    """Add the builder infrastructure assets to an already classified release."""
    import hashlib
    import re
    import tempfile
    name = release['release']
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*', name):
        raise ValueError('release name is not safe for a bootstrap')
    commit = release['hao_patcher']
    if subprocess.check_output(['git', '-C', str(top), 'status', '--porcelain', '--untracked-files=no']).strip():
        raise ValueError('builder kit requires a clean commit')
    if not release.get('candidate'):
        tags = subprocess.check_output(['git', '-C', str(top), 'tag', '--points-at', commit], text=True).splitlines()
        if release['version'] not in tags:
            raise ValueError('final builder cut requires the version tag at the producing commit')
    with tempfile.TemporaryDirectory(prefix='hao-builder-') as temporary:
        folder = Path(stage) if stage else Path(temporary)
        kit = folder / ('hao-kit-' + name + '.tar.gz')
        candidate_key = Path(top)/'tools/hao/data/candidate-keys.pub'
        extra = {}
        if release.get('candidate') and candidate_key.is_file():
            public = candidate_key.read_text().strip()
            if not re.fullmatch(r'ssh-ed25519 [A-Za-z0-9+/=]+(?: [^\n]*)?', public):
                raise ValueError('candidate key must be one Ed25519 public key')
            extra['tools/hao/data/candidate-keys.pub'] = (public+'\n').encode()
        build(top, commit, kit, extra)
        prefix = 'https://github.com/lythka/hao-assets/releases/download/' + name + '/'
        def entry(path, row, role):
            return dict(row=row, file=path.name, url=prefix + path.name,
                        size=path.stat().st_size, sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                        commit=commit, repo='hao-patcher', format='file', role=role,
                        reason='Frozen release builder infrastructure')
        kit_row = entry(kit, 'hao_builder_kit', 'builder-kit')
        pins = {'kit': {k: kit_row[k] for k in ('url', 'sha256', 'size')}}
        for arch in ('arm64', 'x86_64'):
            row = next(x for x in release['downloads'] if x['row'] == 'python_' + arch)
            row['upstream_url'] = row['url']
            row['url'] = prefix + row['name']
            if stage:
                import shutil
                folders = [Path(top)/'pipeline/inputs/golden'/release['golden_build'],
                           Path(top)/'pipeline/inputs'/release['donor_build'], Path(top)/'pipeline/inputs/common']
                lock = next(f/row['row'] for f in folders if (f/row['row']).is_file())
                line = next(l for l in lock.read_text().splitlines() if l.strip() and not l.startswith('#'))
                source = Path(top)/line.split(None, 1)[1]
                if hashlib.sha256(source.read_bytes()).hexdigest() != row['sha256']:
                    raise ValueError('Python source hash mismatch')
                shutil.copyfile(source, folder/row['name'])
            pins[row['row']] = {k: row[k] for k in ('url', 'sha256', 'size')}
        for row in release['assets']:
            if not row['url'].startswith(prefix):
                row['upstream_url'] = row['url']
                row['url'] = prefix + row['file']
                if stage:
                    (folder/'external'/row['file']).replace(folder/row['file'])
        if stage and (folder/'external').is_dir() and not any((folder/'external').iterdir()):
            (folder/'external').rmdir()
        metal = next((x for x in release['assets'] if x['row'] == 'metal_llvm'), None)
        if metal:
            pins['metal'] = {k: metal[k] for k in ('url', 'sha256', 'size')}
        release['bootstrap'] = pins
        template = subprocess.check_output(['git', '-C', str(top), 'show', commit + ':pipeline/bootstrap.sh']).decode()
        key = subprocess.check_output(['git', '-C', str(top), 'show', commit + ':tools/hao/data/release-keys.pub']).decode().strip()
        if '\n' in key:
            raise ValueError('bootstrap expects one owner public key')
        if extra:
            key += '\nhao-release ' + public
        bootstrap = folder / 'hao'
        bootstrap.write_text(template.replace('@RELEASE@', name).replace('@KEY@', key).replace('@CANDIDATE@', 'true' if release.get('candidate') else 'false'))
        bootstrap.chmod(0o755)
        release['assets'] += [kit_row, entry(bootstrap, 'hao_bootstrap', 'bootstrap')]
