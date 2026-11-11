"""Make the already installed Golden Gate System run natively on Intel (run by src/kc-hook.sh).

The base layers (pipeline/stages/golden-image.py) already copied Golden Gate's System/Data and installed the Tahoe
donors and OCLP's hardware overlay; nothing here replaces or keeps System trees. This installs the combined OS cryptex
(Golden Gate SystemOS + RosettaOS x86_64 cache + Tahoe's x86_64 DriverKit cache, with the cache repairs), the OCLP
compat libraries, the display-state shim and its re-signed clients, and disables jobs that cannot run on this Mac."""
import argparse
from contextlib import contextmanager, ExitStack
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import stat
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
from macho import add_load_dylib, combine, section_ranges, set_install_name
from cache_pages import resign_file
from legacy_trackpad import plan_cache_family, apply_edits
from endpoint_security import plan_cache_family as endpoint_plan
from core_routine import plan_cache_family as routine_plan
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


@contextmanager
def volume_mount(device, folder):
    info = disk_info(device)
    previous = info.get('MountPoint')
    if previous:
        # chown/ditto keep owners only with ownership enabled; do not rely on an earlier hook or the builder's history
        run('diskutil', 'enableOwnership', device)
        yield Path(previous)
    else:
        folder.mkdir(mode=0o755)
        run('diskutil', 'mount', '-mountOptions', 'nobrowse', '-mountPoint', folder, device)
        run('diskutil', 'enableOwnership', device)
        try:
            yield folder
        finally:
            run('diskutil', 'unmount', device)


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


# Tahoe programs kept for their x86_64 code but serving 27.0 clients take the 27.0 program's identity: its embedded
# Info.plist (__TEXT,__info_plist), entitlements and identifier, re-signed ad hoc. Tahoe's
# WindowServer launcher traps in BSServiceConnectionListener when 27.0's SkyLight listens on BKTouchStream, a
# BoardServices service that only 27.0's embedded BSServiceDomains declares (entitlements alone did not help).
RESIGN_WITH_27_ENTITLEMENTS = (
    'System/Library/PrivateFrameworks/SkyLight.framework/Versions/A/Resources/WindowServer',
)
# 27.0 jobs that cannot run on this Mac, shipped with Disabled=true (the key Apple uses for a job it does not start), in
# the job's plist and in its entry of launchd's job cache. hybridsearchd (HybridSearchRuntime) stops itself on any Mac
# without Apple silicon ("Not supported on non-Apple silicon device") and launchd restarts it: a crash loop.
DISABLED_JOBS = {'com.apple.hybridsearchd': 'System/Library/LaunchAgents/com.apple.hybridsearchd.plist'}
LAUNCHD_CACHE = 'System/Library/xpc/launchd.plist'


def disabled_job(data, label, job_path):
    """PLIST bytes (a job plist, or launchd's cache keyed by '/' + job_path) with that job's Disabled set to true, in the
    file's own format. Apple's value is a feature-flag condition; it becomes the plain boolean. A missing job stops."""
    fmt = plistlib.FMT_BINARY if data.startswith(b'bplist') else plistlib.FMT_XML
    root = plistlib.loads(data)
    job = root['LaunchDaemons']['/' + job_path] if 'LaunchDaemons' in root else root
    if job.get('Label') != label:
        raise SystemExit('disable %s: found %r at %s' % (label, job.get('Label'), job_path))
    job['Disabled'] = True
    return plistlib.dumps(root, fmt=fmt, sort_keys=False)


def disable_jobs(system):
    done = {}
    for label, job_path in DISABLED_JOBS.items():
        for rel in (job_path, LAUNCHD_CACHE):
            target = system/rel
            data = disabled_job(target.read_bytes(), label, job_path)
            run('chflags', 'noschg,nouchg,norestricted', target)
            target.write_bytes(data)
            os.chown(target, 0, 0)
            os.chmod(target, 0o644)
            run('chflags', 'restricted', target)
            done.setdefault(label, []).append(rel)
            print('Disabled job:', label, 'in', rel, flush=True)
    return done


CPU_X86_64 = 0x01000007


def load_display_installer():
    # An explicit source input also works in flattened guest hooks and in sets
    # without the optional WindowServer filter patch. Its row pins the helper.
    path = Path(__file__).resolve().parent/'inputs/display_install_helper'
    loader = importlib.machinery.SourceFileLoader('display_install_helper', str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def embedded_info_plist(path):
    data = path.read_bytes()
    found = {data[o:o+n] for o, n in section_ranges(data, b'__TEXT', b'__info_plist').values()}
    if len(found) != 1:
        raise SystemExit(f'{path}: expected one embedded Info.plist, found {len(found)}')
    return found.pop()


def replace_info_plist(kept, golden):
    """Write the 27.0 program's embedded Info.plist, without the whitespace between tags, into each slice of the
    kept program; the section cannot grow, so a plist that does not fit stops the build."""
    plist = re.sub(rb'>\s+<', b'><', plistlib.dumps(plistlib.loads(embedded_info_plist(golden)), sort_keys=False))
    data = bytearray(kept.read_bytes())
    sections = section_ranges(data, b'__TEXT', b'__info_plist')
    if not sections:
        raise SystemExit(f'{kept}: no embedded Info.plist')
    for offset, size in sections.values():
        if len(plist) > size:
            raise SystemExit(f'{kept}: 27.0 Info.plist is {len(plist)} bytes, the section holds {size}')
        data[offset:offset+size] = plist + b'\n'*(size-len(plist))
    kept.write_bytes(data)
    return len(plist)


def resign_with_entitlements(kept, golden, workspace, dylibs=()):
    run('chflags', 'noschg,nouchg,norestricted', kept)
    replace_info_plist(kept, golden)
    if dylibs:
        data = bytearray(kept.read_bytes())
        for dylib in dylibs:
            add_load_dylib(data, ('/'+dylib).encode(), CPU_X86_64)
        kept.write_bytes(data)
    entitlements = workspace/(kept.name+'.entitlements.plist')
    entitlements.write_bytes(run('codesign', '-d', '--entitlements', '-', '--xml', golden, output=True))
    plistlib.loads(entitlements.read_bytes())   # a 27.0 program without entitlements would be a recipe error
    details = subprocess.run(['codesign', '-dv', str(golden)], check=True, capture_output=True, text=True).stderr
    identifier = next(l.split('=', 1)[1] for l in details.splitlines() if l.startswith('Identifier='))
    metadata = kept.stat()
    run('codesign', '-f', '-s', '-', '-i', identifier, '--entitlements', entitlements, kept)
    os.chflags(kept, metadata.st_flags)
    return identifier


def install_file(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    os.chown(target, 0, 0)
    os.chmod(target, 0o755)


# airportd SIGBUS without this: OCLP's Modern Wireless wrapper (PatcherSupportPkg 13.7.2-25 IO80211, kept as
# an OCLP path) exports 7 functions 27.0's callers use as 8-byte data (a pointer to a CFString of the name), so a call
# from WiFiPolicy (-[WiFiUsageLinkSession eventDictionary:] -> apple80211_authtype_uppertoStr) runs the wrapper's
# __DATA, and it lacks Apple80211Get20MhzChannelIndexBitmap, which WiFiAnalytics binds. The compat library (row
# io80211_compat, made and checked against the build's clients by src/io80211/make) defines the 8 as the build's
# x86_64 IO80211 does and re-exports the wrapper, renamed IO80211OCLP.dylib, so every other name resolves as before
# and the wrapper's initializers still run (it re-exports IO80211Old.dylib itself).
IO80211 = 'System/Library/PrivateFrameworks/IO80211.framework/Versions/A/'
# coreaudiod, AirPlayXPCHelper, ControlCenter and sharingd abort in libobjc without this: OCLP's Modern Wireless
# WiFiPeerToPeer wrapper (13.7.2-25) lacks 36 Wi-Fi Aware classes 27.x's x86_64 cache binds, so dyld sets them to
# 0xbad4007 and loading APTransport's category on WiFiAwareDatapathInfo is fatal. The compat library (row
# wifip2p_compat, made and checked by src/wifip2p/make) defines them and re-exports the wrapper, renamed
# WiFiPeerToPeerOCLP.dylib, in the same way.
WIFIP2P = 'System/Library/PrivateFrameworks/WiFiPeerToPeer.framework/Versions/A/'


def install_io80211(system, compat, workspace):
    install_wrapper_compat(system, compat, workspace, IO80211, 'IO80211')


def install_wifip2p(system, compat, workspace):
    install_wrapper_compat(system, compat, workspace, WIFIP2P, 'WiFiPeerToPeer')


def install_wrapper_compat(system, compat, workspace, folder, leaf):
    """Rename OCLP's wrapper SYSTEM/FOLDER/LEAF to LEAFOCLP.dylib (install name and file, re-signed with its
    identifier) and install COMPAT, which re-exports it, as LEAF."""
    wrapper, renamed = system/folder/leaf, system/folder/f'{leaf}OCLP.dylib'
    data = bytearray(wrapper.read_bytes())
    set_install_name(data, ('/'+folder+leaf).encode(), ('/'+folder+f'{leaf}OCLP.dylib').encode())
    details = subprocess.run(['codesign', '-dv', str(wrapper)], check=True, capture_output=True, text=True).stderr
    identifier = next(l.split('=', 1)[1] for l in details.splitlines() if l.startswith('Identifier='))
    staged = workspace/f'{leaf}OCLP.dylib'
    staged.write_bytes(data)
    run('codesign', '-f', '-s', '-', '-i', identifier, '--preserve-metadata=entitlements,flags,runtime', staged)
    flags = wrapper.stat().st_flags
    run('chflags', 'noschg,nouchg,norestricted', wrapper)
    for source, target in ((staged, renamed), (compat, wrapper)):
        install_file(source, target)
        os.chflags(target, flags)
    print(f'{leaf}: compat library re-exports OCLP\'s wrapper as', renamed.name, 'identifier', identifier, flush=True)


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


def install_cryptex(payload, preboot, group, golden_os, rosetta, workspace, driverkit_artifact, donor_tool):
    current = preboot/group/'cryptex1/current'
    work = current/'.golden-gate-os.dmg'
    # Resizing either source container hangs in APFSContainerGetMinimalSize.
    # Format a new raw image at its final size instead; never resize a live APFS container.
    size = logical_size(golden_os)+logical_size(rosetta)+(2 << 30)
    with work.open('xb') as stream:
        stream.truncate((size+(1 << 20)-1)//(1 << 20)*(1 << 20))
    metadata = golden_os.stat()
    volume_name = disk_info(golden_os)['VolumeName']
    attached = plistlib.loads(run('hdiutil', 'attach', '-nomount', '-plist',
                                  '-imagekey', 'diskimage-class=CRawDiskImage', work, output=True))
    whole = attached['system-entities'][0]['dev-entry']
    try:
        run(f'{APFS_RESOURCES}/newfs_apfs',
            '-i', '-U', metadata.st_uid, '-G', metadata.st_gid, '-v', volume_name, whole)
    finally:
        run('hdiutil', 'detach', whole)
    counts = dict(added_subtrees=0, universal_files=0)
    with image_mount(work, workspace/'combined-os', writable=True) as mount:
        replace_tree(golden_os, mount, {'.fseventsd', '.Spotlight-V100', '.Trashes'})
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
        os.chmod(mount, stat.S_IMODE(metadata.st_mode))
        os.chflags(mount, metadata.st_flags)
        caches = mount/'System/Library/dyld'
        counts['cache_architectures'] = [name for name in ('arm64e', 'x86_64')
                                        if (caches/f'dyld_shared_cache_{name}').is_file()]
    counts['seal'] = seal_image(work)
    counts['container'] = 'Fresh raw APFS container; Golden Gate SystemOS and RosettaOS files; sealed'
    print('Combined cryptex:', json.dumps(counts), flush=True)
    remove(current/'os.clone.dmg')
    remove(current/'os.dmg')
    os.replace(work, current/'os.dmg')
    run('cp', '-c', current/'os.dmg', current/'os.clone.dmg')
    run('sh', Path(__file__).with_name('cryptex-cache-alias.sh'), preboot)
    counts['rosetta_cache_alias'] = 'Cryptexes/Rosetta/System/Library/dyld -> ../../../OS/System/Library/dyld'
    remove(current/'app.dmg')
    run('cp', payload/'app.dmg', current/'app.dmg')
    return counts


def arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('system', 'payload', 'shim', 'io80211', 'wifip2p'):
        parser.add_argument(name, type=Path)
    parser.add_argument('--driverkit-artifact', type=Path, required=True,
                        help='tahoe_driverkit_donors artifact (x86_64 DriverKit cache for the cryptex)')
    parser.add_argument('--donor-tool', type=Path, required=True, help='tools/tahoe-donors/make')
    return parser.parse_args(argv)


def main():
    args = arguments()
    system, payload, shim, io80211, wifip2p = args.system, args.payload, args.shim, args.io80211, args.wifip2p
    display_installer = load_display_installer()
    group, preboot_device = os.environ['GRP'], os.environ['PBU']
    metadata = json.loads((payload/'userspace.json').read_text())
    # Never recursively clean mountpoints after a failed unmount: Preboot is
    # writable and a TemporaryDirectory cleanup would walk its still-mounted files.
    workspace = Path(tempfile.mkdtemp(prefix='golden-gate-'))
    with ExitStack() as stack:
        source = stack.enter_context(image_mount(payload/'filesystem.dmg', workspace/'filesystem'))
        golden_os = stack.enter_context(image_mount(payload/'os.dmg', workspace/'golden-os'))
        rosetta = stack.enter_context(image_mount(payload/'rosetta.dmg', workspace/'rosetta'))
        preboot = stack.enter_context(volume_mount(preboot_device, workspace/'preboot'))
        disabled = disable_jobs(system)
        display_installer.install_library(system, shim, install_file)
        install_io80211(system, io80211, workspace)
        install_wifip2p(system, wifip2p, workspace)
        display_installer.resign_clients(system, source, workspace, RESIGN_WITH_27_ENTITLEMENTS,
                                        resign_with_entitlements)
        counts = install_cryptex(payload, preboot, group, golden_os, rosetta, workspace,
                                 driverkit_artifact=args.driverkit_artifact, donor_tool=args.donor_tool)
        version = system/'System/Library/CoreServices/SystemVersion.plist'
        for rel in ('System/Library/CoreServices/SystemVersion.plist',
                    'boot/System/Library/CoreServices/SystemVersion.plist',
                    'cryptex1/current/SystemVersion.plist'):
            target = preboot/group/rel
            target.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
            remove(target)
            run('ditto', version, target)
        metadata.update(installation_order="golden-first", disabled_jobs=disabled,
                        cryptex=counts,
                        boot_metadata='Tahoe Intel bootloader, collection layout and cryptex tickets retained',
                        validation='Built only; full Golden Gate boot and desktop untested')
        record = system/'System/Library/CoreServices/golden-gate-userspace.json'
        # Golden Gate's System supplies CoreServices; create it explicitly (0755) if it ever does not.
        record.parent.mkdir(mode=0o755, exist_ok=True)
        record.write_text(json.dumps(metadata, indent=2)+'\n')
        os.chmod(record, 0o644)
        print('Installed userspace:', plistlib.loads(version.read_bytes()), flush=True)
    shutil.rmtree(workspace)


if __name__ == '__main__':
    main()
