#!/usr/bin/env python3
"""guest_a0.py SHARE WORK: stage A0 of the Golden Gate Recovery, as root in the builder VM (never on the host).

Makes a BaseSystem whose "macOS Base System" volume holds the 27.2.1 (26B131) BaseSystem tree on the Intel boot
path of the main image:
  1. the Tahoe BaseSystem (SHARE/in/tahoe.dmg: its container, Base System + Preboot volumes, volume UUID and
     .root_uuid) -> UDRW copy in WORK, grown to GROW; the Base System volume emptied and filled with the 26B131 tree
     (SHARE/in/gg.dmg)
  2. from the main image (attached to the VM read-only as a USB disk; its System and Preboot volumes and the OS
     cryptex os.dmg are mounted read-only): every Mach-O of the tree whose slices are all, byte for byte, slices of
     the main image's file at the same path is replaced by that file (universal or hao-embedded); usr/lib/dyld,
     usr/libexec/hao and usr/lib/hao (no aot) as installed there; /System/Library/Extensions replaced by its tree;
     BootKernelExtensions.kc (+ .elides) and SystemKernelExtensions.kc, the latter also as
     BaseSystemKernelExtensions.kc; Tahoe boot.efi and PlatformSupport.plist; the x86_64 shared cache (OS cryptex)
     in /System/Library/dyld beside the BaseSystem's own arm64e cache, and the x86_64 DriverKit cache
  3. A0 access (SHARE/access): a marker job (zsh, its lines on /dev/console = serial) and sshd per connection on
     port 22 for root by key, both under /usr/local/a0 and /System/Library/LaunchDaemons
  4. Preboot: the main image's BootKC under <uuid>/boot, its boot.efi and PlatformSupport.plist, 26B131's
     SystemVersion.plist
  5. SHARE/out: BaseSystem.dmg (UDRO), disk.img (the container as a raw GPT disk: QEMU boots the volume through
     OpenCore without a RAM disk) and dmgdisk.img (raw GPT disk, one HFS+ volume holding
     com.apple.recovery.boot/BaseSystem.dmg: OpenCore's DmgLoading path)
Env: GROW (default 20g). Log SHARE/out/a0.log ("STEP name seconds" lines); SHARE/out/a0.json the record; the last
log line is "RESULT rc=N".
"""
import json, os, plistlib, re, shutil, subprocess, sys, time
from pathlib import Path

SHARE, WORK = Path(sys.argv[1]), Path(sys.argv[2])
OUT = SHARE / 'out'
GROW = os.environ.get('GROW', '20g')
SPECIAL = {'.file', '.vol', '.nofollow', '.resolve', '.fseventsd', '.Spotlight-V100', '.Trashes'}
SKIP_TREES = ('System/Library/dyld', 'System/Library/Extensions', 'System/Library/KernelCollections', 'usr/lib/hao',
              'usr/lib/dyld', 'usr/libexec/hao', 'bin/zsh', 'usr/lib/zsh')
record = dict(operation='gg-recovery-a0', grow=GROW, steps=[], commands=[])
attached, mounted = [], []
T0 = time.monotonic()


def log(*a):
    print(*a, flush=True)


def run(*args, check=True, quiet=False):
    args = [str(a) for a in args]
    r = subprocess.run(args, capture_output=True, text=True)
    record['commands'].append(dict(command=args, rc=r.returncode))
    if not quiet or r.returncode:
        log('+ ' + ' '.join(args) + ('' if not r.returncode else '  -> rc=%d' % r.returncode))
        out = (r.stdout + r.stderr).strip()
        if out and (r.returncode or len(out) < 2000):
            log(out)
    if check and r.returncode:
        raise RuntimeError('%s failed (%d)' % (args[0], r.returncode))
    return r.stdout


def plist(*args):
    return plistlib.loads(run(*args, quiet=True).encode())


def step(name, t):
    s = round(time.monotonic() - t, 1)
    record['steps'].append(dict(name=name, seconds=s))
    log('STEP %s %s s' % (name, s))


def attach(image, *extra):
    """hdiutil attach -nomount; returns (whole disk, [entities])."""
    p = plistlib.loads(run('hdiutil', 'attach', '-plist', '-nomount', '-noverify', '-noautofsck', *extra, image,
                           quiet=True).encode())
    ents = p['system-entities']
    whole = sorted((e['dev-entry'] for e in ents if re.fullmatch(r'/dev/disk\d+', e['dev-entry'])),
                   key=lambda d: (len(d), d))[0]
    attached.append(whole)
    return whole, ents


def apfs_volumes(dev):
    """APFS volumes of the container(s) whose physical store is on disk dev: [{name, device, uuid, roles}]."""
    want = dev.removeprefix('/dev/')
    out = []
    for c in plist('diskutil', 'apfs', 'list', '-plist')['Containers']:
        stores = [s['DeviceIdentifier'] for s in c['PhysicalStores']]
        if any(s == want or s.startswith(want + 's') for s in stores) or c['ContainerReference'] == want:
            out += [dict(name=v['Name'], device=v['DeviceIdentifier'], uuid=v['APFSVolumeUUID'],
                         roles=v.get('Roles', [])) for v in c['Volumes']]
    return out


def apfs_children(whole):
    """APFS volumes on whole disk or on the synthesized container disk(s) hdiutil made for it."""
    vols = apfs_volumes(whole)
    if vols:
        return vols
    for c in plist('diskutil', 'apfs', 'list', '-plist')['Containers']:
        for s in c['PhysicalStores']:
            if s['DeviceIdentifier'].startswith(whole.removeprefix('/dev/') + 's'):
                return apfs_volumes(c['ContainerReference'])
    return []


def mount(device, at, readonly=True):
    at.mkdir(parents=True, exist_ok=True)
    run('diskutil', 'unmount', device, check=False, quiet=True)
    run('mount', '-t', 'apfs', '-o', 'nobrowse' + (',rdonly' if readonly else ''), '/dev/' + device.removeprefix('/dev/'), at)
    mounted.append(at)


def volume(vols, name=None, role=None):
    hits = [v for v in vols if (name is None or v['name'] == name) and (role is None or role in v['roles'])]
    if len(hits) != 1:
        raise RuntimeError('want one volume name=%s role=%s, have %s' % (name, role, vols))
    return hits[0]


# ---- Mach-O slices ----
def slices(data):
    """[(cputype, cpusubtype, bytes)] of a thin or fat Mach-O, or None when data is not one."""
    if len(data) < 32:
        return None
    m = data[:4]
    if m in (b'\xcf\xfa\xed\xfe', b'\xce\xfa\xed\xfe'):
        return [(int.from_bytes(data[4:8], 'little'), int.from_bytes(data[8:12], 'little'), data)]
    if m in (b'\xca\xfe\xba\xbe', b'\xca\xfe\xba\xbf'):
        n = int.from_bytes(data[4:8], 'big')
        if not 0 < n < 32:
            return None
        wide = m[3] == 0xbf
        out, at = [], 8
        for _ in range(n):
            ct, st = int.from_bytes(data[at:at + 4], 'big'), int.from_bytes(data[at + 4:at + 8], 'big')
            if wide:
                off, size = int.from_bytes(data[at + 8:at + 16], 'big'), int.from_bytes(data[at + 16:at + 24], 'big')
                at += 32
            else:
                off, size = int.from_bytes(data[at + 8:at + 12], 'big'), int.from_bytes(data[at + 12:at + 16], 'big')
                at += 20
            if off + size > len(data):
                return None
            out.append((ct, st, data[off:off + size]))
        return out
    return None


X86_64 = 0x01000007


def carried(bs, main):
    """True when main has an x86_64 slice and every slice of bs is, byte for byte, a slice of main."""
    b, m = slices(bs), slices(main)
    if not b or not m or not any(ct == X86_64 for ct, _, _ in m):
        return False
    have = {(ct, st): d for ct, st, d in m}
    return all(have.get((ct, st)) == d for ct, st, d in b)


def overlay_machos(bs, sysroot):
    counts = dict(machos=0, replaced=0, no_counterpart=0, differs=0, no_x86_64=0)
    lists = dict(replaced=[], differs=[], no_counterpart=[], no_x86_64=[])
    for dirpath, dirs, files in os.walk(bs):
        rel = os.path.relpath(dirpath, bs)
        if rel == '.':
            dirs[:] = [d for d in dirs if d not in SPECIAL]
        if any(rel == t or rel.startswith(t + '/') for t in SKIP_TREES):
            dirs[:] = []
            continue
        for f in files:
            p = os.path.join(dirpath, f)
            r = os.path.normpath(os.path.join(rel, f))
            if os.path.islink(p) or not os.path.isfile(p) or any(r == t for t in SKIP_TREES):
                continue
            with open(p, 'rb') as s:
                head = s.read(4)
            if head not in (b'\xcf\xfa\xed\xfe', b'\xce\xfa\xed\xfe', b'\xca\xfe\xba\xbe', b'\xca\xfe\xba\xbf'):
                continue
            data = Path(p).read_bytes()
            if slices(data) is None:
                continue
            counts['machos'] += 1
            q = os.path.join(sysroot, r)
            if os.path.islink(q) or not os.path.isfile(q):
                counts['no_counterpart'] += 1; lists['no_counterpart'].append(r)
                continue
            main = Path(q).read_bytes()
            if carried(data, main):
                os.unlink(p)
                run('ditto', q, p, quiet=True)
                counts['replaced'] += 1; lists['replaced'].append(r)
            elif slices(main) and not any(ct == X86_64 for ct, _, _ in slices(main)):
                counts['no_x86_64'] += 1; lists['no_x86_64'].append(r)
            else:
                counts['differs'] += 1; lists['differs'].append(r)
    return counts, lists


ARM64 = 0x0100000c


def trust_basesystem(bs, paths):
    """hao runs an arm64 program only when the kernel registered its signature or its cdhash is in a trusted list; the
    x86_64 kernel registers only 4 KiB pages, and the main image's list (usr/lib/hao/trusted-cdhashes, made by
    hao-runtime from the full OS) does not have the BaseSystem-only programs. For each Mach-O the overlay left
    arm64-only, every arm64/arm64e slice that satisfies `anchor apple` (this arm64 builder's codesign) is appended
    as "CDHASH  PATH", the list's own format; the rest are counted, never listed."""
    lst = bs / 'usr/lib/hao/trusted-cdhashes'
    have = {l.split()[0] for l in lst.read_text().splitlines() if l and not l.startswith('#')}
    added, refused = [], []
    for r in paths:
        f = bs / r
        sl = slices(f.read_bytes())
        archs = sorted({'arm64e' if st & 0xff == 2 else 'arm64' for ct, st, _ in sl or () if ct == ARM64})
        for arch in archs:
            ok = subprocess.run(['codesign', '-v', '--ignore-resources', '-a', arch, '-R=anchor apple', str(f)], capture_output=True)
            if ok.returncode:
                refused.append('%s (%s)' % (r, arch)); continue
            d = subprocess.run(['codesign', '-d', '-vvv', '-a', arch, str(f)], capture_output=True, text=True)
            m = re.search(r'^CDHash=([0-9a-f]{40})$', d.stderr, re.M)
            if not m:
                refused.append('%s (%s): no CDHash' % (r, arch)); continue
            if m.group(1) not in have:
                have.add(m.group(1)); added.append('%s  %s' % (m.group(1), r))
    with open(lst, 'a') as o:
        o.write('# gg-recovery a0: %d BaseSystem programs and libraries (anchor apple, arm64 slices)\n' % len(added))
        o.write(''.join(l + '\n' for l in added))
    os.chown(lst, 0, 0); os.chmod(lst, 0o644)
    return dict(added=len(added), refused=refused)


def ditto_tree(src, dst):
    if dst.exists() or dst.is_symlink():
        run('rm', '-rf', dst)
    run('ditto', src, dst)


def main():
    t = time.monotonic()
    log('builder %s %s, %s' % (run('sw_vers', '-buildVersion', quiet=True).strip(), os.uname().machine,
                               run('id', '-un', quiet=True).strip()))
    run('rm', '-rf', WORK / 'a0'); W = WORK / 'a0'; W.mkdir(parents=True)
    OUT.mkdir(exist_ok=True)

    # the main image (read-only USB disk): the one non-boot container with a System volume
    root = plist('diskutil', 'info', '-plist', '/')
    boot = {root.get('ParentWholeDisk'), root.get('APFSContainerReference')}
    sysvol = None
    for c in plist('diskutil', 'apfs', 'list', '-plist')['Containers']:
        stores = [s['DeviceIdentifier'] for s in c['PhysicalStores']]
        whole = {re.match(r'disk\d+', s).group(0) for s in stores}
        if c['ContainerReference'] in boot or whole & boot:
            continue
        for v in c['Volumes']:
            if 'System' in v.get('Roles', []):
                if sysvol:
                    raise RuntimeError('two non-boot System volumes: %s %s' % (sysvol, v['DeviceIdentifier']))
                sysvol = dict(system=v, preboot=next(x for x in c['Volumes'] if 'Preboot' in x.get('Roles', [])),
                              container=c['ContainerReference'])
    if not sysvol:
        raise RuntimeError('no System volume of the main image attached')
    SYS, PB, OS, GG = W / 'sys', W / 'pb', W / 'os', W / 'gg'
    mount(sysvol['system']['DeviceIdentifier'], SYS)
    mount(sysvol['preboot']['DeviceIdentifier'], PB)
    groups = [d for d in PB.iterdir() if re.fullmatch(r'[0-9A-F-]{36}', d.name)]
    grp, = groups
    osdmg = grp / 'cryptex1/current/os.dmg'
    _, ents = attach(osdmg, '-readonly')
    osdev = next(e['dev-entry'] for e in ents if e.get('content-hint', '').startswith('41504653'))
    mount(osdev, OS)
    sv = plistlib.loads((SYS / 'System/Library/CoreServices/SystemVersion.plist').read_bytes())
    record['main'] = dict(system=sysvol['system']['APFSVolumeUUID'], preboot_group=grp.name,
                          build=sv['ProductBuildVersion'], version=sv['ProductVersion'])
    log('main image %s (%s), group %s' % (sv['ProductVersion'], sv['ProductBuildVersion'], grp.name))

    ggwhole, _ = attach(SHARE / 'in/gg.dmg', '-readonly')
    gv = volume(apfs_children(ggwhole), name='macOS Base System')
    mount(gv['device'], GG)
    gsv = plistlib.loads((GG / 'System/Library/CoreServices/SystemVersion.plist').read_bytes())
    log('Golden Gate BaseSystem %s (%s)' % (gsv['ProductVersion'], gsv['ProductBuildVersion']))
    record['basesystem'] = dict(build=gsv['ProductBuildVersion'], version=gsv['ProductVersion'])
    step('setup', t)

    # 1. the Tahoe container, grown, its Base System volume refilled with the 26B131 tree
    t = time.monotonic()
    shutil.copyfile(SHARE / 'in/tahoe.dmg', W / 'tahoe.dmg')
    run('hdiutil', 'convert', W / 'tahoe.dmg', '-format', 'UDRW', '-o', W / 'rw.dmg', quiet=True)
    os.unlink(W / 'tahoe.dmg')
    run('hdiutil', 'resize', '-size', GROW, '-imageonly', W / 'rw.dmg')
    rwwhole, _ = attach(W / 'rw.dmg')
    part = rwwhole + 's1'
    run('diskutil', 'apfs', 'resizeContainer', part.removeprefix('/dev/'), '0')
    vols = apfs_children(rwwhole)
    bv, pv = volume(vols, name='macOS Base System'), volume(vols, name='Preboot')
    BS, BPB = W / 'bs', W / 'bpb'
    mount(bv['device'], BS, readonly=False)
    mount(pv['device'], BPB, readonly=False)
    record['container'] = dict(basesystem=bv['uuid'], preboot=pv['uuid'])
    for e in BS.iterdir():
        if e.name not in SPECIAL:
            run('rm', '-rf', e, quiet=True)
    for e in GG.iterdir():
        if e.name in SPECIAL:
            continue
        if e.is_symlink():
            os.symlink(os.readlink(e), BS / e.name)
            st = os.lstat(e); os.lchown(BS / e.name, st.st_uid, st.st_gid)
        else:
            run('ditto', e, BS / e.name, quiet=True)
    step('tree', t)

    # 2. the main image's files
    t = time.monotonic()
    counts, lists = overlay_machos(BS, SYS)
    record['machos'] = counts
    (OUT / 'machos.json').write_text(json.dumps(dict(counts=counts, **lists), indent=1) + '\n')
    log('Mach-O: %s' % counts)
    step('machos', t)

    t = time.monotonic()
    for r in ('usr/lib/dyld', 'usr/libexec/hao', 'bin/zsh'):   # the BaseSystem has no zsh of its own
        (BS / r).unlink(missing_ok=True)
        run('ditto', SYS / r, BS / r)
    if (SYS / 'usr/lib/zsh').is_dir():
        ditto_tree(SYS / 'usr/lib/zsh', BS / 'usr/lib/zsh')
    (BS / 'usr/lib/hao').mkdir(exist_ok=True)
    for e in (SYS / 'usr/lib/hao').iterdir():
        if e.name != 'aot':
            ditto_tree(e, BS / 'usr/lib/hao' / e.name)
    td = BS / 'System/Library/Templates/Data'
    # rc.temporaryDataVolume ditto's Templates/Data onto a tmpfs Data volume. Writing the com.apple.rootless xattr
    # (and SF_RESTRICTED) there is refused (EPERM) when the BaseSystem is booted from a disk, even with
    # csr-active-config 0xfef: only a booter that sets kBootArgsFlagCSRBoot (a Recovery boot) lifts it for ditto, and
    # the failed copy panics the boot (restore-datapartition). The RAM Data volume keeps no protected state, so the
    # template loses those xattrs and flags.
    record['template_rootless_xattrs'] = int(run('sh', '-c', 'xattr -r "$1" | grep -c ": com.apple.rootless$" || true', 'x', td, quiet=True))
    run('xattr', '-r', '-d', 'com.apple.rootless', td, check=False)
    record['template_flags_cleared'] = int(run('sh', '-c', 'find "$1" \\( -flags +restricted -o -flags +sunlnk \\) | wc -l', 'x', td, quiet=True))
    run('chflags', '-R', 'norestricted,nosunlnk', td)
    tmpl = BS / 'System/Library/Templates/Data/private/var/db/hao'
    tmpl.mkdir(parents=True, exist_ok=True); os.chmod(tmpl, 0o755)
    ditto_tree(SYS / 'System/Library/Extensions', BS / 'System/Library/Extensions')
    kc, mkc = BS / 'System/Library/KernelCollections', SYS / 'System/Library/KernelCollections'
    kc.mkdir(exist_ok=True)
    for n in ('BootKernelExtensions.kc', 'BootKernelExtensions.kc.elides', 'SystemKernelExtensions.kc'):
        run('ditto', mkc / n, kc / n)
    run('cp', '-c', kc / 'SystemKernelExtensions.kc', kc / 'BaseSystemKernelExtensions.kc')
    cs = BS / 'System/Library/CoreServices'
    for n in ('boot.efi', 'PlatformSupport.plist'):
        (cs / n).unlink(missing_ok=True)
        run('ditto', grp / 'System/Library/CoreServices' / n, cs / n)
    (cs / 'bootbase.efi').unlink(missing_ok=True)
    run('cp', '-c', cs / 'boot.efi', cs / 'bootbase.efi')
    dy = BS / 'System/Library/dyld'
    x86 = sorted(p for p in (OS / 'System/Library/dyld').iterdir() if p.name.startswith('dyld_shared_cache_x86_64'))
    for p in x86:
        run('ditto', p, dy / p.name, quiet=True)
    dk = OS / 'System/DriverKit/System/Library/dyld'
    if dk.is_dir():
        (BS / 'System/DriverKit/System/Library/dyld').mkdir(parents=True, exist_ok=True)
        for p in dk.iterdir():
            if 'x86_64' in p.name:
                run('ditto', p, BS / 'System/DriverKit/System/Library/dyld' / p.name, quiet=True)
    record['x86_64_cache'] = [dict(name=p.name, size=p.stat().st_size) for p in x86]
    record['trust'] = trust_basesystem(BS, lists['no_counterpart'] + lists['differs'] + lists['no_x86_64'])
    log('trust: %d cdhashes added, %d slices refused' % (record['trust']['added'], len(record['trust']['refused'])))
    step('main-files', t)

    # 3. A0 access
    t = time.monotonic()
    a = BS / 'usr/local/a0'
    a.mkdir(parents=True, exist_ok=True)
    acc = SHARE / 'access'
    for n, mode in (('marker.zsh', 0o755), ('sshd_config', 0o644), ('ssh_host_ed25519_key', 0o600),
                    ('authorized_keys', 0o600)):
        shutil.copyfile(acc / n, a / n); os.chown(a / n, 0, 0); os.chmod(a / n, mode)
    for n in ('local.a0.marker.plist', 'local.a0.ssh.plist'):
        d = BS / 'System/Library/LaunchDaemons' / n
        shutil.copyfile(acc / n, d); os.chown(d, 0, 0); os.chmod(d, 0o644)
    run('chown', '-R', 'root:wheel', a)
    step('access', t)

    # 4. Preboot
    t = time.monotonic()
    pg = BPB / bv['uuid']
    if not pg.is_dir():
        raise RuntimeError('Preboot has no folder for the Base System volume %s' % bv['uuid'])
    pk = pg / 'boot/System/Library/KernelCollections'
    pk.mkdir(parents=True, exist_ok=True)
    for n in ('BootKernelExtensions.kc', 'BootKernelExtensions.kc.elides'):
        (pk / n).unlink(missing_ok=True)
        run('ditto', mkc / n, pk / n)
    pcs = pg / 'System/Library/CoreServices'
    for src, n in ((grp / 'System/Library/CoreServices/boot.efi', 'boot.efi'),
                   (grp / 'System/Library/CoreServices/PlatformSupport.plist', 'PlatformSupport.plist'),
                   (GG / 'System/Library/CoreServices/SystemVersion.plist', 'SystemVersion.plist')):
        (pcs / n).unlink(missing_ok=True)
        run('ditto', src, pcs / n)
    run('ls', '-lnR', pg)
    step('preboot', t)

    # 5. out
    t = time.monotonic()
    log(run('df', '-h', BS, quiet=True))
    record['used_bytes'] = shutil.disk_usage(BS).used
    for m in (BPB, BS):
        run('sync'); run('umount', m); mounted.remove(m)
    run('hdiutil', 'detach', rwwhole); attached.remove(rwwhole)
    for n in ('BaseSystem.dmg', 'disk.img', 'dmgdisk.img'):
        (OUT / n).unlink(missing_ok=True)
    run('hdiutil', 'convert', W / 'rw.dmg', '-format', 'UDRO', '-o', W / 'BaseSystem.dmg', quiet=True)
    run('hdiutil', 'convert', W / 'rw.dmg', '-format', 'UDTO', '-o', W / 'disk', quiet=True)
    os.unlink(W / 'rw.dmg')
    dsz = (W / 'BaseSystem.dmg').stat().st_size
    record['dmg_bytes'] = dsz
    log('BaseSystem.dmg %d bytes (%.2f GiB)' % (dsz, dsz / 2**30))
    mb = dsz // 2**20 + 1024
    run('hdiutil', 'create', '-size', '%dm' % mb, '-fs', 'HFS+', '-volname', 'GGA0', '-layout', 'GPTSPUD',
        W / 'dmgdisk.dmg')
    hwhole, ents = attach(W / 'dmgdisk.dmg')
    hfs = next(e['dev-entry'] for e in ents if 'Apple_HFS' in e.get('content-hint', '') or
               e.get('content-hint') == '48465300-0000-11AA-AA11-00306543ECAC')
    H = W / 'hfs'; H.mkdir()
    run('mount', '-t', 'hfs', '-o', 'nobrowse', hfs, H); mounted.append(H)
    (H / 'com.apple.recovery.boot').mkdir()
    run('ditto', W / 'BaseSystem.dmg', H / 'com.apple.recovery.boot/BaseSystem.dmg')
    run('umount', H); mounted.remove(H)
    run('hdiutil', 'detach', hwhole); attached.remove(hwhole)
    run('hdiutil', 'convert', W / 'dmgdisk.dmg', '-format', 'UDTO', '-o', W / 'dmgdisk', quiet=True)
    os.unlink(W / 'dmgdisk.dmg')
    for src, n in ((W / 'BaseSystem.dmg', 'BaseSystem.dmg'), (W / 'disk.cdr', 'disk.img'),
                   (W / 'dmgdisk.cdr', 'dmgdisk.img')):
        shutil.copyfile(src, OUT / n); os.unlink(src)
    step('out', t)


def cleanup():
    for m in reversed(mounted):
        run('umount', '-f', m, check=False, quiet=True)
    for d in reversed(attached):
        run('hdiutil', 'detach', '-force', d, check=False, quiet=True)


if __name__ == '__main__':
    os.umask(0o022)
    rc = 0
    try:
        main()
    except Exception as e:
        import traceback
        traceback.print_exc()
        log('FAIL: %s' % e)
        record['error'] = str(e)
        rc = 1
    finally:
        cleanup()
        record['seconds'] = round(time.monotonic() - T0, 1)
        OUT.mkdir(exist_ok=True)
        (OUT / 'a0.json').write_text(json.dumps(record, indent=1) + '\n')
    log('total %s s' % record['seconds'])
    log('RESULT rc=%d' % rc)
    sys.exit(rc)
