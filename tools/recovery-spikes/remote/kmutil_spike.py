"""Spike (b): link the Darwin 27 Boot and System kernel collections in the Recovery. Runs ON THE LAPTOP.
   kmutil_spike.py T [auto|full|boot]
The kmutil invocations are patches/darwin27-kernel/src/kc-hook.sh's, unchanged: Tahoe's kmutil (input row
tahoe_kmutil, re-signed ad hoc on the host as the hook does), -R over a root whose driver tree is the installed
image's, -k the Darwin 27 kernel (row darwin27_kernel), build directory /.darwin27-kc-build inside the root, boot
first, member list by members.py, then sys with --elide-identifier per boot member.

The driver tree comes from the internal disk's System volume, mounted READ-ONLY (or used where the Recovery already
mounted it) and copied into a fresh APFS volume in a raw image file on a second RAM disk; nothing on the internal
disk is written. Scope "full" copies the whole tree and links both collections; "boot" copies only the bundles of
the installed BootKC's members and links the Boot collection (the one the kernel binder binds). "auto" picks full
when the memory allows. Afterwards kc-binder (staged by the host) binds the new BootKC with this Python, and the
Recovery's own /usr/bin/kmutil links the same root once more for comparison."""
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from spikelib import Spike, CommandFailed, container_on, main_guard, mem_available_mb, sha256, tree_bytes  # noqa: E402

SOURCES = ['System/Library/Extensions', 'System/Library/DriverExtensions', 'Library/Extensions',
           'Library/Apple/System/Library/Extensions', 'System/Library/CoreServices/SystemVersion.plist']
EXTENSION_DIRS = [s for s in SOURCES if s.endswith('Extensions')]
B = '/.darwin27-kc-build'   # kc-hook.sh's build path; it is linked into the BootKC
KC = 'System/Library/KernelCollections'
MB = 1 << 20


def internal_systems(listing, store_info):
    """System-role volumes of containers whose every physical store is an internal, non-virtual disk.
    STORE_INFO maps a store device to its `diskutil info -plist`."""
    found = []
    for c in listing['Containers']:
        stores = [st['DeviceIdentifier'] for st in c['PhysicalStores']]
        if not stores or not all(store_info[d].get('Internal') and store_info[d].get('VirtualOrPhysical') != 'Virtual'
                                 for d in stores):
            continue
        found += [v['DeviceIdentifier'] for v in c['Volumes'] if v.get('Roles') == ['System']]
    return found


def bundle_ids(bundle):
    """CFBundleIdentifier of BUNDLE and of every kext nested in it (PlugIns), recursively."""
    ids = set()
    for folder, dirs, names in os.walk(bundle):
        for info in ('Contents/Info.plist', 'Info.plist'):
            if folder.endswith(('.kext', '.dext')) and os.path.isfile(os.path.join(folder, info)):
                try:
                    with open(os.path.join(folder, info), 'rb') as f:
                        ident = plistlib.load(f).get('CFBundleIdentifier')
                except Exception:
                    ident = None
                if ident:
                    ids.add(ident)
                break
    return ids


def select_bundles(root, wanted):
    """Top-level bundles under ROOT's extension folders holding any of WANTED; returns (relative paths, ids found)."""
    picked, found = [], set()
    for rel in EXTENSION_DIRS:
        folder = Path(root)/rel
        if not folder.is_dir():
            continue
        for entry in sorted(folder.iterdir()):
            if entry.suffix not in ('.kext', '.dext') or entry.is_symlink():
                continue
            ids = bundle_ids(entry)
            if ids & wanted:
                picked.append('%s/%s' % (rel, entry.name))
                found |= ids & wanted
    return picked, found


def need_mb(data_bytes, full):
    """RAM disk for a root holding DATA_BYTES: APFS and image overhead, the kernel, and the collections kmutil writes."""
    return int(data_bytes*1.15/MB) + (640 if full else 256)


def choose_scope(requested, full_mb, boot_mb, available_mb, margin_mb=1024):
    if requested == 'full' or (requested == 'auto' and full_mb+margin_mb <= available_mb):
        return 'full' if full_mb+margin_mb <= available_mb else None
    if requested in ('auto', 'boot'):
        return 'boot' if boot_mb+margin_mb <= available_mb else None
    raise ValueError('scope must be auto, full or boot')


def members(s, kc):
    p = s.run(sys.executable, '-I', s.scratch/'code/patches/darwin27-kernel/src/members.py', kc, check=False)
    return p.stdout.decode().split() if p.returncode == 0 else None


def copy_paths(s, source_root, root, paths):
    """tar from SOURCE_ROOT into ROOT, owners and modes kept, file flags not (restricted cannot be set here)."""
    src = subprocess.Popen(['tar', '-C', source_root, '-cf', '-', '--no-fflags']+paths, stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE)
    dst = subprocess.run(['tar', '-C', root, '-xpf', '-', '--no-fflags'], stdin=src.stdout, capture_output=True)
    src.stdout.close()
    err = src.stderr.read().decode(errors='replace')
    src.wait()
    s.log.write('$ tar copy %d paths rc=%d/%d\n%s%s' % (len(paths), src.returncode, dst.returncode, err[-4000:],
                                                       dst.stderr.decode(errors='replace')[-4000:]))
    return src.returncode, dst.returncode


def link(s, label, tool, root, scope):
    build = root/B.lstrip('/')
    if build.exists():
        shutil.rmtree(build)
    build.mkdir()
    args = ['-a', 'x86_64', '--allow-missing-kdk', '-R', root, '-k', root/'System/Library/Kernels/kernel',
            '-V', 'release', '-B', B+'/BootKernelExtensions.kc']
    out = {}
    with s.timed('link-boot:'+label):
        p = s.run(tool, 'create', '-n', 'boot', *args, check=False)
    (s.results/('kmutil-%s-boot.txt' % label)).write_bytes((p.stdout or b'')+(p.stderr or b''))
    boot = build/'BootKernelExtensions.kc'
    ids = members(s, boot) if p.returncode == 0 and boot.is_file() else None
    s.check('kmutil-boot:'+label, ids is not None and 'com.apple.kernel' in ids,
            'rc=%d, %s' % (p.returncode, '%d members, %d MB, sha256 %s' % (len(ids), boot.stat().st_size//MB,
                                                                          sha256(boot)[:16]) if ids else 'no collection'))
    if ids is None:
        return None
    out['boot'] = boot
    out['members'] = ids
    elides = [i for i in ids if i != 'com.apple.kernel']
    (build/'BootKernelExtensions.kc.elides').write_text(''.join(i+'\n' for i in elides))
    if scope != 'full':
        s.check('kmutil-sys:'+label, None, 'boot scope: only the Boot collection was linked')
        return out
    with s.timed('link-sys:'+label):
        p = s.run(tool, 'create', '-n', 'sys', *args, '-S', B+'/SystemKernelExtensions.kc',
                  *[w for i in elides for w in ('--elide-identifier', i)], check=False)
    (s.results/('kmutil-%s-sys.txt' % label)).write_bytes((p.stdout or b'')+(p.stderr or b''))
    sysk = build/'SystemKernelExtensions.kc'
    sids = members(s, sysk) if p.returncode == 0 and sysk.is_file() else None
    s.check('kmutil-sys:'+label, sids is not None,
            'rc=%d, %s' % (p.returncode, '%d members, %d MB' % (len(sids), sysk.stat().st_size//MB) if sids else
                           'no collection'))
    if sids is not None:
        out['sys'] = sysk
    return out


def body(s):
    requested = sys.argv[2] if len(sys.argv) > 2 else 'auto'
    inputs = s.scratch/'inputs'

    listing = s.plist('diskutil', 'apfs', 'list', '-plist')
    stores = {st['DeviceIdentifier'] for c in listing['Containers'] for st in c['PhysicalStores']}
    found = internal_systems(listing, {d: s.plist('diskutil', 'info', '-plist', d) for d in stores})
    if not s.check('internal-system-found', len(found) == 1, 'System volumes on internal disks: %s' % (found or 'none')):
        return
    device = found[0]
    info = s.plist('diskutil', 'info', '-plist', device)
    if info.get('MountPoint'):
        sysroot = Path(info['MountPoint'])
        s.info('internal-mount', 'already mounted by the Recovery at %s (writable %s); only read'
               % (sysroot, info.get('WritableVolume')))
    else:
        sysroot = s.scratch/'mnt/internal-system'
        sysroot.mkdir(parents=True)
        s.remember('mount', sysroot)
        s.run('diskutil', 'mount', 'readOnly', '-mountOptions', 'nobrowse', '-mountPoint', sysroot, device)
        info = s.plist('diskutil', 'info', '-plist', device)
        s.check('internal-readonly', info.get('WritableVolume') is False, '%s at %s, writable %s'
                % (device, sysroot, info.get('WritableVolume')))
    with open(sysroot/'System/Library/CoreServices/SystemVersion.plist', 'rb') as f:
        s.result('internal_build', plistlib.load(f).get('ProductBuildVersion'))
    kernel_sha = sha256(inputs/'kernel')
    internal_kernel = sysroot/'System/Library/Kernels/kernel'
    s.info('kernel-same-as-row', 'yes' if internal_kernel.is_file() and sha256(internal_kernel) == kernel_sha else
           'NO: the installed image has another kernel; members and bytes need not match')
    internal_kc = sysroot/KC/'BootKernelExtensions.kc'
    internal_members = members(s, internal_kc) if internal_kc.is_file() else None
    if not s.check('internal-bootkc', internal_members is not None, '%s %s' % (
            internal_kc, '%d members, sha256 %s' % (len(internal_members), sha256(internal_kc)[:16])
            if internal_members else 'missing or not a fileset')):
        return
    internal_sha = sha256(internal_kc)

    wanted = set(internal_members)-{'com.apple.kernel'}
    boot_paths, boot_found = select_bundles(sysroot, wanted)
    if wanted-boot_found:
        s.info('boot-members-not-found', ' '.join(sorted(wanted-boot_found)[:20]))
    full_paths = [p for p in SOURCES if (sysroot/p).exists()]
    full_bytes = sum(tree_bytes(sysroot/p) if (sysroot/p).is_dir() else (sysroot/p).stat().st_size
                     for p in full_paths)
    boot_bytes = sum(tree_bytes(sysroot/p) for p in boot_paths)
    available = mem_available_mb()
    scope = choose_scope(requested, need_mb(full_bytes, True), need_mb(boot_bytes, False), available)
    s.result('tree_mb', 'full %d (%d paths), boot %d (%d bundles), memory available %d'
             % (full_bytes//MB, len(full_paths), boot_bytes//MB, len(boot_paths), available))
    if not s.check('memory', scope is not None, 'scope %s (asked %s)' % (scope, requested)):
        return
    s.result('scope', scope)
    root_mb = need_mb(full_bytes if scope == 'full' else boot_bytes, scope == 'full')

    _, disk = s.ramdisk('root', root_mb+64)
    image = disk/'kc-root.img'
    with image.open('xb') as f:
        f.truncate(root_mb << 20)
    whole = s.attach_raw(image)
    s.run('diskutil', 'partitionDisk', whole, 'GPT', 'APFS', 'SpikeKC', '100%')
    volume = container_on(s.plist('diskutil', 'apfs', 'list', '-plist'), whole)['Volumes'][0]['DeviceIdentifier']
    s.run('diskutil', 'unmount', volume)
    root = s.scratch/'mnt/kc-root'
    root.mkdir()
    s.remember('mount', root)
    s.run('diskutil', 'mount', '-mountOptions', 'nobrowse', '-mountPoint', root, volume)
    s.run('diskutil', 'enableOwnership', root)

    paths = full_paths if scope == 'full' else boot_paths+['System/Library/CoreServices/SystemVersion.plist']
    with s.timed('copy-tree'):
        rcs = copy_paths(s, sysroot, root, paths)
    copied = tree_bytes(root)
    s.check('tree-copied', rcs == (0, 0), '%d paths, %d MB, tar rc %d/%d' % (len(paths), copied//MB, *rcs))
    others = sum(1 for folder, dirs, names in os.walk(root) for n in dirs+names
                 if (Path(folder)/n).lstat().st_uid != 0)
    if others:
        s.info('tree-owners', '%d entries not owned by root after the copy: chown -R root:wheel (as the build '
                              'normalises its KDK copy)' % others)
        s.run('chown', '-R', 'root:wheel', root/'System', *(['%s/Library' % root] if (root/'Library').exists() else []))
    kernels = root/'System/Library/Kernels'
    kernels.mkdir(parents=True, exist_ok=True)
    s.run('install', '-o', 'root', '-g', 'wheel', '-m', '644', inputs/'kernel', kernels/'kernel')
    (root/KC).mkdir(parents=True, exist_ok=True)

    os.chmod(inputs/'kmutil', 0o755)
    tahoe = link(s, 'tahoe', inputs/'kmutil', root, scope)
    if tahoe:
        keep = s.results/'kc-tahoe'
        keep.mkdir()
        for name in ('BootKernelExtensions.kc', 'BootKernelExtensions.kc.elides', 'SystemKernelExtensions.kc'):
            if (root/B.lstrip('/')/name).exists():
                shutil.copy2(root/B.lstrip('/')/name, keep/name)
        same = tahoe['members'] == internal_members
        diff = sorted(set(tahoe['members']) ^ set(internal_members))[:10]
        s.check('members-equal-internal', same, '%d members%s' % (len(tahoe['members']), '' if same else
                                                                   ', differ: %s' % ' '.join(diff)))
        new_sha = sha256(keep/'BootKernelExtensions.kc')
        s.result('bootkc_sha256', new_sha)
        s.info('bootkc-identical-internal', 'yes' if new_sha == internal_sha else
               'no (internal %s, new %s)' % (internal_sha[:16], new_sha[:16]))
        with s.timed('bind'):
            p = s.run(sys.executable, '-I', s.scratch/'code/kcstage/tools/kc-binder/kc-binder', 'bind',
                      '--boot-kc', keep/'BootKernelExtensions.kc', '--out', s.results/'bind-tahoe', check=False)
        tail = ((p.stderr or b'')+(p.stdout or b'')).decode(errors='replace').strip().splitlines()
        s.check('kc-bind', p.returncode == 0, 'kc-binder bind rc=%d%s' % (p.returncode, '' if p.returncode == 0 else
                                                                         ': '+(tail[-1] if tail else '')))
    else:
        for name in ('members-equal-internal', 'kc-bind'):
            s.check(name, None, 'no Boot collection')

    recovery = Path('/usr/bin/kmutil')
    if recovery.is_file():
        s.info('recovery-kmutil', 'sha256 %s' % sha256(recovery)[:16])
        other = link(s, 'recovery', recovery, root, scope)
        if other and tahoe:
            s.info('recovery-kmutil-result', 'members %s as Tahoe kmutil; Boot collection %s' % (
                'same' if other['members'] == tahoe['members'] else 'NOT the same',
                'identical' if sha256(other['boot']) == new_sha else 'different bytes'))
    else:
        s.info('recovery-kmutil', 'absent from the Recovery')


if __name__ == '__main__':
    main_guard(Spike('kmutil', sys.argv[1]), body)
