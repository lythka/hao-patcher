"""Build the sealed OS and App cryptex images (golden-gate-userspace; called by install.py).

This module is everything that decides the images' bytes besides the inputs: install.py's other work (the compat
libraries, WindowServer, the disabled jobs, the record) is outside it, so a change there keeps a cached image
(cryptex_cache.py keys an image on this module, the local modules it imports and build/, which make.sh fills)."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import plistlib
import shutil
import stat
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from macho import combine
from cache_pages import resign_file
from legacy_trackpad import plan_cache_family, apply_edits
from endpoint_security import plan_cache_family as endpoint_plan
from core_routine import plan_cache_family as routine_plan
import swift_refs
from codeid_carrier import plan_cache_family as codeid_plan
import cryptex_stubs


def matrix_plan(cache):
    package = Path(__file__).resolve().parent / 'build/metal-fixes'
    result = subprocess.check_output([sys.executable, '-I', str(package/'metal-fixes'),
                                      'mps-plan', '--cache', str(cache)], text=True)
    return {Path(path): [(offset, bytes.fromhex(before), bytes.fromhex(after), reason)
                         for offset, before, after, reason in edits]
            for path, edits in json.loads(result).items()}


def audio_plan(cache):
    package = Path(__file__).resolve().parent / 'build/audiomxd-fix'
    result = subprocess.check_output([sys.executable, '-I', str(package/'audiomxd-fix'),
                                      'plan', '--cache', str(cache)], text=True)
    return {cache/name: [(offset, bytes.fromhex(before), bytes.fromhex(after), reason)
                        for offset, before, after, reason in edits]
            for name, edits in json.loads(result).items()}


def run(*args, output=False):
    command = [str(arg) for arg in args]
    if output:
        return subprocess.check_output(command)
    subprocess.run(command, check=True)


def disk_info(device):
    return plistlib.loads(run('diskutil', 'info', '-plist', device, output=True))


@contextmanager
def image_mount(image, folder, writable=False):
    folder.mkdir(mode=0o755)
    options = [] if writable else ['-readonly']
    info = plistlib.loads(run('hdiutil', 'attach', *options, '-nobrowse', '-owners', 'on',
                              '-mountpoint', folder, '-plist', image, output=True))
    device = info['system-entities'][0]['dev-entry']
    try:
        yield folder
    finally:
        run('hdiutil', 'detach', device)


def protected(path, keep):
    return path in keep or any(path.startswith(item+'/') for item in keep)


def ancestor(path, keep):
    return any(item.startswith(path+'/') for item in keep)


def remove(path):
    if not os.path.lexists(path):
        return
    if path.is_symlink():
        run('chflags', '-h', 'noschg,nouchg,norestricted', path)
        path.unlink()
        return
    run('chflags', '-RP', 'noschg,nouchg,norestricted', path)
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()


def copy_path(source, target):
    # ditto follows a symlink passed as its top-level source argument.
    if source.is_symlink():
        shutil.copy2(source, target, follow_symlinks=False)
        metadata = source.lstat()
        os.lchown(target, metadata.st_uid, metadata.st_gid)
    else:
        run('ditto', '--rsrc', '--extattr', '--acl', source, target)
        restore_special_modes(source, target)


def restore_special_modes(source, target):
    """Restore source set-ID modes after copying; never traverse symlinks.

    The Golden input's native login is 04555, but image 1881cef shipped
    byte-identical login as 0555. Without setuid it cannot query audit state.
    Apply modes after all ownership/copy operations, which can clear them.
    """
    metadata = source.lstat()
    if stat.S_ISLNK(metadata.st_mode):
        return
    if metadata.st_mode & (stat.S_ISUID | stat.S_ISGID):
        current = target.lstat()
        if stat.S_IFMT(current.st_mode) != stat.S_IFMT(metadata.st_mode):
            raise ValueError(f'Copy changed file type: {target}')
        mode = stat.S_IMODE(metadata.st_mode)
        if stat.S_IMODE(current.st_mode) != mode:
            os.chmod(target, mode, follow_symlinks=False)
    if stat.S_ISDIR(metadata.st_mode):
        for child in source.iterdir():
            restore_special_modes(child, target/child.name)


def replace_tree(source, target, keep, relative='', anchors=frozenset()):
    """Replace complete subtrees, recursing only around explicitly retained paths."""
    if target.is_symlink():
        raise ValueError(f'Retained-path ancestor is a symlink: {relative}')
    target.mkdir(mode=0o755, parents=True, exist_ok=True)
    entries = list(source.iterdir()) if source is not None else []
    names = {p.name for p in entries}
    for old in target.iterdir():
        rel = '/'.join(filter(None, (relative, old.name)))
        if old.name in names or protected(rel, keep):
            continue
        if rel in anchors or ancestor(rel, keep | anchors):
            replace_tree(None, old, keep, rel, anchors)
        else:
            remove(old)
    for item in entries:
        rel = '/'.join(filter(None, (relative, item.name)))
        destination = target/item.name
        if protected(rel, keep):
            continue
        if rel in anchors or ancestor(rel, keep | anchors):
            if not item.is_dir() or item.is_symlink():
                raise ValueError(f'Preserved descendant of non-directory: {rel}')
            replace_tree(item, destination, keep, rel, anchors)
        else:
            remove(destination)
            copy_path(item, destination)


def merge_rosetta(source, target, counts):
    """Keep ARM resources and add the native Intel cache and missing signed slices."""
    for item in source.iterdir():
        if item.name.startswith('.'):
            continue
        destination = target/item.name
        if not os.path.lexists(destination):
            copy_path(item, destination)
            counts['added_subtrees'] += 1
        elif item.is_dir() and not item.is_symlink() and destination.is_dir() and not destination.is_symlink():
            merge_rosetta(item, destination, counts)
        elif item.is_file() and not item.is_symlink() and destination.is_file() and not destination.is_symlink():
            with item.open('rb') as stream:
                magic = stream.read(4)
            if magic not in (b'\xcf\xfa\xed\xfe', b'\xce\xfa\xed\xfe', b'\xca\xfe\xba\xbe',
                              b'\xca\xfe\xba\xbf', b'\xbe\xba\xfe\xca', b'\xbf\xba\xfe\xca'):
                continue
            original = destination.read_bytes()
            merged = combine(original, item.read_bytes())
            if merged is not None and merged != original:
                metadata = destination.stat()
                run('chflags', 'noschg,nouchg,norestricted', destination)
                temporary = destination.with_name('.golden-'+destination.name)
                # ditto retains xattrs and ownership; writing then replaces only file bytes.
                run('ditto', destination, temporary)
                temporary.write_bytes(merged)
                os.replace(temporary, destination)
                os.chown(destination, metadata.st_uid, metadata.st_gid)
                os.chmod(destination, stat.S_IMODE(metadata.st_mode))
                os.chflags(destination, metadata.st_flags)
                counts['universal_files'] += 1


def logical_size(root):
    return sum((Path(folder)/name).lstat().st_size
               for folder, _, names in os.walk(root) for name in names)


APFS_RESOURCES = '/System/Library/Filesystems/apfs.fs/Contents/Resources'


def seal_image(image):
    """Seal the APFS volume of a raw image (unmounted) and return its root hash line.

    27.0's PID-1 ignition grafts the OS cryptex with root-hash authentication
    and APFS refuses an unsealed image ("Graft of an unsealed image (volume
    type: 4) is not allowed"). A sealed image grafts with
    authenticated-root disabled even when its hash differs from the ticket's
    (Apple's 27.0 SystemOS grafts with Tahoe's root_hash sidecar), so the combined image only has to be sealed."""
    attached = plistlib.loads(run('hdiutil', 'attach', '-nomount', '-plist',
                                  '-imagekey', 'diskimage-class=CRawDiskImage', image, output=True))
    entities = attached['system-entities']
    whole = entities[0]['dev-entry']
    volume = next(e['dev-entry'] for e in entities if e.get('content-hint', '').startswith('41504653'))
    try:
        out = run(f'{APFS_RESOURCES}/apfs_prepare_cryptex', volume, output=True).decode(errors='replace')
        sealed = plistlib.loads(run('diskutil', 'info', '-plist', volume, output=True)).get('Sealed')
        if sealed not in ('Yes', True):
            raise RuntimeError(f'{image}: volume not sealed after apfs_prepare_cryptex ({sealed!r})')
    finally:
        run('hdiutil', 'detach', whole)
    return next((line for line in out.splitlines() if 'root hash' in line.lower()), out.strip()[-200:])


def new_image(work, size, like):
    """A new raw image WORK of SIZE bytes (rounded up to 1 MiB) with one APFS volume named and owned like the mounted
    volume LIKE. Resizing a source container hangs in APFSContainerGetMinimalSize: never resize a live one."""
    with work.open('xb') as stream:
        stream.truncate((size+(1 << 20)-1)//(1 << 20)*(1 << 20))
    metadata = like.stat()
    volume_name = disk_info(like)['VolumeName']
    attached = plistlib.loads(run('hdiutil', 'attach', '-nomount', '-plist',
                                  '-imagekey', 'diskimage-class=CRawDiskImage', work, output=True))
    whole = attached['system-entities'][0]['dev-entry']
    try:
        run(f'{APFS_RESOURCES}/newfs_apfs',
            '-i', '-U', metadata.st_uid, '-G', metadata.st_gid, '-v', volume_name, whole)
    finally:
        run('hdiutil', 'detach', whole)


CRYPTEX_KEEP = {'.fseventsd', '.Spotlight-V100', '.Trashes'}


def build_os_image(work, golden_os, rosetta, workspace, driverkit_artifact, donor_tool, codeid_carrier, hao):
    """Build the combined OS cryptex as the new raw image WORK, sealed, and return its record: Golden Gate's SystemOS
    files, RosettaOS's x86_64 cache, Tahoe's x86_64 DriverKit cache, the cache repairs, the code identity carrier, the
    4 KiB re-signing and the hao stubs of SystemOS's arm64-only programs. Reads only GOLDEN_OS, ROSETTA, the two donor
    arguments, CODEID_CARRIER, HAO and this folder's code (cryptex_cache.source_files): those are its cache key."""
    # Format a new raw image at its final size.
    new_image(work, logical_size(golden_os)+logical_size(rosetta)+(2 << 30), golden_os)
    metadata = golden_os.stat()
    counts = dict(added_subtrees=0, universal_files=0)
    with image_mount(work, workspace/'combined-os', writable=True) as mount:
        replace_tree(golden_os, mount, CRYPTEX_KEEP)
        # hao stubs of the arm64-only programs in SystemOS (Safari's and WebKit's XPC services), before sealing.
        counts['hao_stubs'] = cryptex_stubs.embed(hao, 'OS', mount)
        merge_rosetta(rosetta, mount, counts)
        # 27.x ships DriverKit only for arm64e: add the explicitly extracted Tahoe x86_64 DriverKit cache family.
        driverkit = 'System/DriverKit/System/Library/dyld'
        run(sys.executable, '-I', donor_tool, 'install-driverkit', '--artifact', driverkit_artifact, '--cryptex', mount)
        counts['driverkit_x86_64'] = sorted(p.name for p in (mount/driverkit).glob('dyld_shared_cache_x86_64*'))
        # RosettaOS signs the x86_64 cache with 16 KiB code pages for Apple silicon; the
        # x86_64 kernel refuses that signature (dyld: "code signature registration for
        # shared cache failed"). Re-sign it with 4 KiB pages before sealing.
        trackpad_plan = plan_cache_family(mount/'System/Library/dyld')
        counts['legacy_trackpad_v3'] = {
            path.name: [reason for _, _, _, reason in changes]
            for path, changes in trackpad_plan.items()}
        es_plan = endpoint_plan(mount/'System/Library/dyld')
        counts['endpoint_security_selectors'] = {
            path.name: [reason for _, _, _, reason in changes]
            for path, changes in es_plan.items()}
        for path, changes in es_plan.items():
            trackpad_plan.setdefault(path, []).extend(changes)
        routine = routine_plan(mount/'System/Library/dyld')
        counts['core_routine_console_uid'] = {
            path.name: [reason for _, _, _, reason in changes]
            for path, changes in routine.items()}
        for path, changes in routine.items():
            trackpad_plan.setdefault(path, []).extend(changes)
        matrix = matrix_plan(mount/'System/Library/dyld')
        counts['mps_matrix_initializer'] = {
            path.name: [reason for _, _, _, reason in changes]
            for path, changes in matrix.items()}
        for path, changes in matrix.items():
            trackpad_plan.setdefault(path, []).extend(changes)
        audio = audio_plan(mount/'System/Library/dyld')
        counts['audio_no_console_notification'] = {
            path.name: [reason for _, _, _, reason in changes]
            for path, changes in audio.items()}
        for path, changes in audio.items():
            trackpad_plan.setdefault(path, []).extend(changes)
        # Apple's x86_64 cache leaves some Swift conformance type references unadjusted (they point at unrelated
        # bytes; a name lookup that scans them faults). The arm64e cache beside it names the intended types.
        swift = swift_refs.plan_cache_family(mount/'System/Library/dyld')
        counts['swift_conformance_types'] = {
            path.name: [reason for _, _, _, reason in changes]
            for path, changes in swift.items()}
        for path, changes in swift.items():
            trackpad_plan.setdefault(path, []).extend(changes)
        # Code identity carrier: hao_codeid_decide after Security's two validity bodies (src/codeid_carrier.py,
        # row codeid_carrier), so every native and hao process answers listed code's requirement checks from its
        # Apple-signed reference (/usr/lib/hao/stub-identities, native-identities).
        carrier, counts['code_identity_carrier'] = codeid_plan(mount/'System/Library/dyld', codeid_carrier)
        for path, changes in carrier.items():
            trackpad_plan.setdefault(path, []).extend(changes)
        resigned = []
        for cache in sorted((mount/'System/Library/dyld').glob('dyld_shared_cache_x86_64*')):
            if cache.suffix in ('.map', '.atlas') or cache.is_symlink():
                continue
            metadata = cache.stat()
            run('chflags', 'noschg,nouchg,norestricted', cache)
            if cache in trackpad_plan:
                apply_edits(cache, trackpad_plan[cache])
            if resign_file(cache, force=cache in trackpad_plan):
                resigned.append(cache.name)
            os.chflags(cache, metadata.st_flags)
        counts['resigned_4k'] = resigned
        images, totals, issues = swift_refs.verify(mount/'System/Library/dyld', 'x86_64')
        if issues:
            raise RuntimeError(f'x86_64 cache: {len(issues)} bad Swift metadata references, first: {issues[0]}')
        counts['swift_references_x86_64'] = dict(images=images, **totals, bad=0)
        os.chmod(mount, stat.S_IMODE(metadata.st_mode))
        os.chflags(mount, metadata.st_flags)
        caches = mount/'System/Library/dyld'
        counts['cache_architectures'] = [name for name in ('arm64e', 'x86_64')
                                        if (caches/f'dyld_shared_cache_{name}').is_file()]
    counts['seal'] = seal_image(work)
    counts['container'] = 'Fresh raw APFS container; Golden Gate SystemOS and RosettaOS files; sealed'
    return counts


APPLE_APP = "Apple's app.dmg"


def build_app_image(payload, work, workspace, hao):
    """The App cryptex (Safari and its agents) as WORK: Apple's image as it is when the hao payload has no stubs for
    it; otherwise, as for the OS cryptex, a fresh raw image with the same files, the stubs embedded, sealed."""
    if not cryptex_stubs.entries(hao, 'App'):
        run('cp', payload/'app.dmg', work)
        return dict(image=APPLE_APP, hao_stubs=dict(embedded=0, already=0))
    with image_mount(payload/'app.dmg', workspace/'golden-app') as source:
        new_image(work, logical_size(source)+(256 << 20), source)
        metadata = source.stat()
        with image_mount(work, workspace/'combined-app', writable=True) as mount:
            replace_tree(source, mount, CRYPTEX_KEEP)
            stubs = cryptex_stubs.embed(hao, 'App', mount)
            os.chmod(mount, stat.S_IMODE(metadata.st_mode))
            os.chflags(mount, metadata.st_flags)
    seal = seal_image(work)
    return dict(image='Fresh raw APFS container; AppOS files with hao stubs; sealed', hao_stubs=stubs, seal=seal)
