"""Embed the hao payload's stubs for programs that live in a cryptex (called by install.py while it builds the image).

hao-image-payload scans a root that has the App and OS cryptexes where the running system grafts them
(System/Volumes/Preboot/Cryptexes/NAME), so their arm64-only programs (Safari's helpers, WebKit's XPC services) get
manifest entries under that prefix. hao-runtime's hao-install.py installs into the System volume, where those paths
are empty graft points, and counts them as missing. A cryptex is a sealed image, so its stubs are embedded here,
into the files of the image this recipe builds, before it is sealed. The checks are the payload's own (its
hao-install.py): exact source hash, the bundle files the stub binds, and the merged file's recorded hash. Unlike
hao-install.py, every entry of the cryptex must be embedded: the image is built from the exact files the payload saw."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat

PREFIX = 'System/Volumes/Preboot/Cryptexes/'
# Flags that stop an in-place write (the cryptex copy keeps Apple's restricted flag); restored after the write.
LOCKED = stat.SF_IMMUTABLE | stat.UF_IMMUTABLE | getattr(stat, 'SF_RESTRICTED', 0x80000)


def manifest(hao):
    return json.loads((Path(hao)/'embed/manifest.json').read_text())


def entries(hao, name):
    """The payload's manifest entries for cryptex NAME (App, OS), first occurrence of each path."""
    prefix, seen, out = PREFIX + name + '/', set(), []
    for entry in manifest(hao)['files']:
        if entry['path'].startswith(prefix) and entry['path'] not in seen:
            seen.add(entry['path'])
            out.append(entry)
    return out


def rebase(entry, name):
    """ENTRY with its program and bound-file paths relative to the cryptex root; a bound file outside it is refused."""
    prefix = PREFIX + name + '/'

    def strip(path):
        if not path.startswith(prefix) or path == prefix:
            raise ValueError(f'{entry["path"]}: {path} is not in the {name} cryptex')
        return path[len(prefix):]
    bound = [dict(b, file=strip(b['file'])) if 'file' in b else dict(b) for b in entry.get('bound', [])]
    return dict(entry, path=strip(entry['path']), **({'bound': bound} if 'bound' in entry else {}))


def load_installer(hao):
    """The payload's hao-install.py as a module (it imports its hao_fat and hao_codesig from beside itself)."""
    spec = importlib.util.spec_from_file_location('hao_install', Path(hao)/'hao-install.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def embed(hao, name, mount):
    """Embed every stub of cryptex NAME into its files at MOUNT; return the record. Raises if any entry is not embedded."""
    hao, mount = Path(hao).resolve(), str(Path(mount).resolve())
    selected = entries(hao, name)
    if not selected:
        return dict(embedded=0, already=0)
    installer = load_installer(hao)
    installer.manifest_names(manifest(hao), None)
    done, already, errors = [], [], []
    for original in selected:
        try:
            entry = rebase(original, name)
            if entry.get('mode') == 'translate':
                raise ValueError('translate entries are not supported in a cryptex')
            target = installer.inside(mount, entry['path'], final_link=True)
            stub = Path(installer.inside(str(hao/'embed'), entry['stub'])).read_bytes()
        except (ValueError, installer.Unsafe) as error:
            errors.append(f'{original["path"]} ({error})')
            continue
        if os.path.islink(target) or not os.path.isfile(target):
            errors.append(f'{original["path"]} (missing)')
            continue
        why = installer.check_bound(mount, entry, stub)
        if why:
            errors.append(f'{original["path"]} ({why})')
            continue
        data = Path(target).read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        if digest == entry['result_sha256']:
            done.append(entry['path'])
            already.append(entry['path'])
            continue
        if digest != entry['source_sha256']:
            errors.append(f'{original["path"]} (source hash differs)')
            continue
        merged = installer.hao_fat.combine(data, stub, drop_x86_64=False)
        if hashlib.sha256(merged).hexdigest() != entry['result_sha256']:
            errors.append(f'{original["path"]} (merge result differs)')
            continue
        flags = os.lstat(target).st_flags
        if flags & LOCKED:
            os.chflags(target, flags & ~LOCKED, follow_symlinks=False)
        try:
            installer.write_in_place(target, merged)
        finally:
            if flags & LOCKED:
                os.chflags(target, flags, follow_symlinks=False)
        done.append(entry['path'])
    if errors:
        raise RuntimeError(f'{name} cryptex: {len(errors)} of {len(selected)} hao stubs not embedded: '
                           + '; '.join(errors[:10]))
    print(f'{name} cryptex: embedded {len(done)} hao stubs ({len(already)} already)', flush=True)
    return dict(embedded=len(done), already=len(already))
