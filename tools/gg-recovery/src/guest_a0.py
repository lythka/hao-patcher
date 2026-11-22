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
import json, os, plistlib, re, shutil, subprocess, sys, time, zlib
from pathlib import Path

SHARE, WORK = Path(sys.argv[1]), Path(sys.argv[2])
OUT = SHARE / 'out'
GROW = os.environ.get('GROW', '20g')
STAGE = os.environ.get('STAGE', 'a0')
sys.path.insert(0, str(Path(__file__).resolve().parent))
SPECIAL = {'.file', '.vol', '.nofollow', '.resolve', '.fseventsd', '.Spotlight-V100', '.Trashes'}
SKIP_TREES = ('System/Library/dyld', 'System/Library/Extensions', 'System/Library/KernelCollections', 'usr/lib/hao',
              'usr/lib/dyld', 'usr/libexec/hao', 'bin/zsh', 'usr/lib/zsh')
record = dict(operation='gg-recovery-' + STAGE, stage=STAGE, grow=GROW, steps=[], commands=[])
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


def merge_trust(bs, payload):
    """A1: hao's own BaseSystem payload (row hao_runtime_basesystem, hao-trust-list over the stock BaseSystem: every
    program and guest library, the slice hao runs, cdhash as xnu computes it) replaces A0's codesign pass. libhao and
    usr/libexec/hao must be the main image's bytes (same hao-runtime commit); the main image's list stays for the files
    the overlay carried over, its stub-identities and interface units stay as they are (the BaseSystem payload has
    none). The two trusted-cdhashes lists are merged: one line per cdhash."""
    for r in ('usr/lib/hao/libhao.dylib', 'usr/lib/hao/libhao_identity.dylib', 'usr/libexec/hao'):
        if (bs / r).read_bytes() != (payload / 'root' / r).read_bytes():
            raise RuntimeError('%s differs between the main image and the BaseSystem payload' % r)
    lst = bs / 'usr/lib/hao/trusted-cdhashes'
    lines, seen, heads = [], set(), []
    for src in (payload / 'root/usr/lib/hao/trusted-cdhashes', lst):
        for l in src.read_text().splitlines():
            if not l.strip():
                continue
            if l.startswith('#'):
                heads.append(l)
                continue
            h = l.split()[0].lower()
            if h not in seen:
                seen.add(h); lines.append(l)
    main_n = sum(1 for l in lst.read_text().splitlines() if l.strip() and not l.startswith('#'))
    lst.write_text('\n'.join(heads + ['# gg-recovery a1: the lists above merged, one line per cdhash'] + lines) + '\n')
    os.chown(lst, 0, 0); os.chmod(lst, 0o644)
    return dict(lines=len(lines), main=main_n, payload_manifest=json.loads((payload / 'payload-manifest.json').read_text())['version'])


def bundle_ids(sle):
    """{bundle id: [path of its .kext relative to sle]} for every kext under sle, nested ones included."""
    out = {}
    for dirpath, dirs, files in os.walk(sle):
        if dirpath.endswith('.kext') and 'Contents' in dirs:
            ip = Path(dirpath) / 'Contents/Info.plist'
            if ip.is_file():
                try:
                    i = plistlib.loads(ip.read_bytes())
                    out.setdefault(i['CFBundleIdentifier'], []).append(os.path.relpath(dirpath, sle))
                except Exception:
                    pass
    return out


def kc_members(kc):
    """Bundle identifiers of an x86_64 kernel collection (LC_FILESET_ENTRY), as darwin27-kernel's members.py."""
    import struct
    b = Path(kc).read_bytes()
    magic, cpu, _, ftype, n, _ = struct.unpack_from('<IIIIII', b)
    if (magic, cpu & 0xffffff, ftype) != (0xfeedfacf, 7, 12):
        raise RuntimeError('%s: not an x86_64 MH_FILESET' % kc)
    off, ids = 32, []
    for _ in range(n):
        cmd, size = struct.unpack_from('<II', b, off)
        if cmd == 0x80000035:
            name = struct.unpack_from('<I', b, off + 24)[0]
            ids.append(b[off + name:off + size].split(b'\0', 1)[0].decode())
        off += size
    return sorted(ids)


def link_basesystem_kc(bs, sys_root, tahoe_kc, kmutil):
    """A1: BaseSystemKernelExtensions.kc linked by Tahoe kmutil (row tahoe_kmutil) around the main image's BootKC and
    Darwin 27 kernel from the main image's Intel driver tree (already at bs/S/L/E), for Tahoe's BaseSystem list
    (its .bundles) plus the rec-* kexts.txt entries (SHARE/rec-kexts.txt), eliding the BootKC's members. As
    darwin27-kernel's kc-hook: -R is the root being built (kmutil records each kext's path under it, and kexts load
    their resources from that path at boot), the build folder lives inside that root and is removed after."""
    K = WORK / 'a1-kc'; run('rm', '-rf', K); K.mkdir()
    shutil.copyfile(kmutil, K / 'kmutil'); os.chmod(K / 'kmutil', 0o755)
    run('codesign', '-s', '-', '-f', K / 'kmutil', quiet=True)
    kc = bs / 'System/Library/KernelCollections'
    have = bundle_ids(bs / 'System/Library/Extensions')
    want, missing = [], []
    rec = [l.strip() for l in (SHARE / 'rec-kexts.txt').read_text().splitlines() if l.strip() and not l.startswith('#')]
    for i in [l.strip() for l in (tahoe_kc / 'BaseSystemKernelExtensions.kc.bundles').read_text().split()] + rec:
        if i in want:
            continue
        (want if i in have else missing).append(i)
    elides = (kc / 'BootKernelExtensions.kc.elides').read_text().split()
    kernel = bs / 'System/Library/Kernels/kernel'
    had_kernel = kernel.exists()
    if not had_kernel:
        kernel.parent.mkdir(parents=True, exist_ok=True)
        run('ditto', sys_root / 'System/Library/Kernels/kernel', kernel)
    B = '/.gg-kc-build'
    (bs / B.lstrip('/')).mkdir()
    run('ditto', kc / 'BootKernelExtensions.kc', bs / B.lstrip('/') / 'BootKernelExtensions.kc')
    args = [K / 'kmutil', 'create', '-n', 'sys', '-a', 'x86_64', '--allow-missing-kdk', '-V', 'release', '-R', bs,
            '-k', kernel, '-B', B + '/BootKernelExtensions.kc', '-S', B + '/BaseSystemKernelExtensions.kc', '-x']
    for i in want:
        args += ['-b', i]
    for i in elides:
        args += ['--elide-identifier', i]
    r = subprocess.run([str(a) for a in args], capture_output=True, text=True)
    (OUT / 'kmutil.log').write_text(' '.join(map(str, args)) + '\n' + r.stdout + r.stderr)
    built = bs / B.lstrip('/') / 'BaseSystemKernelExtensions.kc'
    if r.returncode or not built.is_file():
        log((r.stdout + r.stderr)[-4000:])
        raise RuntimeError('kmutil rc=%d' % r.returncode)
    for n in ('SystemKernelExtensions.kc', 'BaseSystemKernelExtensions.kc', 'BaseSystemKernelExtensions.kc.bundles',
              'BaseSystemKernelExtensions.kc.elides'):
        (kc / n).unlink(missing_ok=True)
    run('ditto', built, kc / 'BaseSystemKernelExtensions.kc')
    (kc / 'BaseSystemKernelExtensions.kc.bundles').write_text(''.join(i + '\n' for i in sorted(want)))
    (kc / 'BaseSystemKernelExtensions.kc.elides').write_text(''.join(i + '\n' for i in elides))
    for n in ('BaseSystemKernelExtensions.kc', 'BaseSystemKernelExtensions.kc.bundles', 'BaseSystemKernelExtensions.kc.elides'):
        os.chown(kc / n, 0, 0); os.chmod(kc / n, 0o644)
    run('rm', '-rf', bs / B.lstrip('/'))
    if not had_kernel:
        kernel.unlink()
        if not any(kernel.parent.iterdir()):
            kernel.parent.rmdir()
    members = kc_members(kc / 'BaseSystemKernelExtensions.kc')
    return dict(requested=len(want), missing=missing, members=len(members), rec_kexts=len(rec),
                bytes=(kc / 'BaseSystemKernelExtensions.kc').stat().st_size, member_ids=members)


def prune_extensions(bs, keep_ids, strip):
    """A1: /S/L/E keeps only the kexts the Boot and BaseSystem collections hold (their Info.plists and resources load
    from the recorded path); the top-level bundles holding none go, nested kexts not in a collection go, and with
    STRIP each kept kext loses its kernel executable (the code is in the collection), as Tahoe's BaseSystem has it."""
    sle = bs / 'System/Library/Extensions'
    ids = bundle_ids(sle)
    keep_paths = {p for i in keep_ids for p in ids.get(i, [])}
    tops = {p.split('/')[0] for p in keep_paths}
    before = int(run('du', '-sk', sle, quiet=True).split()[0]) * 1024
    removed = []
    for e in sorted(sle.iterdir()):
        if e.name not in tops:
            removed.append(e.name)
            run('rm', '-rf', e, quiet=True)
    stripped = nested = 0
    for i, paths in ids.items():
        for p in paths:
            d = sle / p
            if not d.exists():
                continue
            if p not in keep_paths:
                if '/' in p:
                    run('rm', '-rf', d, quiet=True); nested += 1
                continue
            if strip:
                try:
                    exe = plistlib.loads((d / 'Contents/Info.plist').read_bytes()).get('CFBundleExecutable')
                except Exception:
                    exe = None
                if exe and (d / 'Contents/MacOS' / exe).is_file():
                    (d / 'Contents/MacOS' / exe).unlink(); stripped += 1
    after = int(run('du', '-sk', sle, quiet=True).split()[0]) * 1024
    return dict(bytes_before=before, bytes_after=after, top_level_removed=len(removed), nested_removed=nested,
                executables_stripped=stripped, kept_kexts=len(keep_paths),
                not_found=sorted(i for i in keep_ids if i not in ids))


def tahoe_strips(tahoe_sle):
    """True when Tahoe's own BaseSystem kexts carry no kernel executables (then A1 strips ours the same way)."""
    n = withexe = 0
    for dirpath, dirs, files in os.walk(tahoe_sle):
        if dirpath.endswith('.kext') and 'Contents' in dirs:
            n += 1
            if (Path(dirpath) / 'Contents/MacOS').is_dir() and any((Path(dirpath) / 'Contents/MacOS').iterdir()):
                withexe += 1
    return dict(kexts=n, with_executable=withexe)


MH_EXECUTE, MH_DYLINKER, MH_DYLIB, MH_BUNDLE = 2, 7, 6, 8


def keep_roots(bs, extra):
    """Install names whose closure the trimmed x86_64 cache keeps: (1) every dylib an x86_64 executable, dylib or
    bundle in the BaseSystem tree loads; (2) every image of the BaseSystem's own arm64e cache (Apple's set for this
    Recovery: what its programs link and dlopen, and the images hao routes guest calls to through interface units);
    (3) EXTRA (src/keep-extra.txt)."""
    import dsc
    roots, files = set(), 0
    for dirpath, dirs, fs in os.walk(bs):
        rel = os.path.relpath(dirpath, bs)
        if rel == '.':
            dirs[:] = [d for d in dirs if d not in SPECIAL]
        if rel in ('System/Library/dyld', 'System/DriverKit'):
            dirs[:] = []
            continue
        for f in fs:
            p = os.path.join(dirpath, f)
            if os.path.islink(p) or not os.path.isfile(p) or os.path.getsize(p) < 4096:
                continue
            with open(p, 'rb') as s:
                if s.read(4) not in (b'\xcf\xfa\xed\xfe', b'\xca\xfe\xba\xbe', b'\xca\xfe\xba\xbf'):
                    continue
            for ct, _, d in slices(Path(p).read_bytes()) or ():
                if ct == X86_64 and int.from_bytes(d[12:16], 'little') in (MH_EXECUTE, MH_DYLINKER, MH_DYLIB, MH_BUNDLE):
                    files += 1
                    roots.update(dsc.macho_dependencies(d))
    arm = dsc.Cache(str(bs / 'System/Library/dyld/dyld_shared_cache_arm64e'))
    arm_images = {p for p, _ in arm.images}
    arm.close()
    extra = {l.strip() for l in extra.read_text().splitlines() if l.strip() and not l.startswith('#')}
    return roots | arm_images | extra, dict(x86_64_files=files, from_x86_64=len(roots), arm64e_cache_images=len(arm_images),
                                            extra=len(extra))


def zlib_size(paths, level=6):
    """Bytes the files take compressed with zlib in 1 MiB chunks (about what UDZO stores; all-zero chunks free)."""
    total, z = 0, bytes(1 << 20)
    for p in paths:
        with open(p, 'rb') as f:
            for b in iter(lambda: f.read(1 << 20), b''):
                total += 0 if b == z[:len(b)] else len(zlib.compress(b, level))
    return total


def trim_cache(src_dir, dst_dir, bs, extra):
    """A1: the main image's x86_64 cache, trimmed (src/dsc.py) to the closure of keep_roots(), into dst_dir."""
    import dsc
    dst_dir.mkdir(parents=True, exist_ok=True)
    names = sorted(p.name for p in src_dir.iterdir() if p.name.startswith('dyld_shared_cache_x86_64'))
    for n in names:
        run('ditto', src_dir / n, dst_dir / n, quiet=True)
    c = dsc.Cache(str(dst_dir / 'dyld_shared_cache_x86_64'))
    roots, rstats = keep_roots(bs, extra)
    kept, unresolved = dsc.closure(c, sorted(roots))
    stats = dsc.trim(c, set(kept))
    bad = dsc.verify(dsc.Cache(str(dst_dir / 'dyld_shared_cache_x86_64')))
    if any(bad.values()):
        raise RuntimeError('trimmed cache: pages do not match their hashes: %s' % bad)
    on_disk = {p for p in unresolved if (bs / p.lstrip('/')).exists()}
    (OUT / 'keep.txt').write_text(''.join(p + '\n' for p in sorted(kept.values())))
    (OUT / 'unresolved.txt').write_text(''.join(p + '\n' for p in sorted(unresolved - on_disk)))
    files = [dst_dir / n for n in names if not n.endswith(('.map', '.atlas'))]
    return dict(roots=rstats, kept_images=len(kept), uuid=c.main.uuid.hex(), unresolved=len(unresolved - on_disk),
                unresolved_on_disk=len(on_disk), logical_bytes=sum(f.stat().st_size for f in files),
                zlib_bytes=zlib_size(files), **{k: v for k, v in stats.items() if k != 'files'}, files=stats['files'])


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
    # A1 works in a sparse image: what APFS frees (the Tahoe tree, the copies replaced) is unmapped, so it is neither
    # kept nor compressed into the dmg; A0 used a flat UDRW
    RW = W / ('rw.sparseimage' if STAGE == 'a1' else 'rw.dmg')
    run('hdiutil', 'convert', W / 'tahoe.dmg', '-format', 'UDSP' if STAGE == 'a1' else 'UDRW', '-o', RW, quiet=True)
    os.unlink(W / 'tahoe.dmg')
    run('hdiutil', 'resize', '-size', GROW, '-imageonly', RW)
    rwwhole, _ = attach(RW)
    part = rwwhole + 's1'
    run('diskutil', 'apfs', 'resizeContainer', part.removeprefix('/dev/'), '0')
    vols = apfs_children(rwwhole)
    bv, pv = volume(vols, name='macOS Base System'), volume(vols, name='Preboot')
    BS, BPB = W / 'bs', W / 'bpb'
    mount(bv['device'], BS, readonly=False)
    mount(pv['device'], BPB, readonly=False)
    record['container'] = dict(basesystem=bv['uuid'], preboot=pv['uuid'])
    TKC = W / 'tahoe-kc'
    if STAGE == 'a1':   # Tahoe's BaseSystem collection list, and whether its kexts carry executables, before it goes
        TKC.mkdir()
        for n in ('BaseSystemKernelExtensions.kc.bundles', 'BaseSystemKernelExtensions.kc.elides'):
            shutil.copyfile(BS / 'System/Library/KernelCollections' / n, TKC / n)
        record['tahoe_extensions'] = tahoe_strips(BS / 'System/Library/Extensions')
        log('Tahoe BaseSystem kexts: %s' % record['tahoe_extensions'])
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
    # STOCK_TEMPLATE=1 (A1) leaves the template as Apple made it, to test whether a RAM-disk boot sets CSRBoot.
    record['stock_template'] = os.environ.get('STOCK_TEMPLATE') == '1'
    record['template_rootless_xattrs'] = int(run('sh', '-c', 'xattr -r "$1" | grep -c ": com.apple.rootless$" || true', 'x', td, quiet=True))
    record['template_flags'] = int(run('sh', '-c', 'find "$1" \\( -flags +restricted -o -flags +sunlnk \\) | wc -l', 'x', td, quiet=True))
    if not record['stock_template']:
        run('xattr', '-r', '-d', 'com.apple.rootless', td, check=False)
        run('chflags', '-R', 'norestricted,nosunlnk', td)
    tmpl = BS / 'System/Library/Templates/Data/private/var/db/hao'
    tmpl.mkdir(parents=True, exist_ok=True); os.chmod(tmpl, 0o755)
    ditto_tree(SYS / 'System/Library/Extensions', BS / 'System/Library/Extensions')
    kc, mkc = BS / 'System/Library/KernelCollections', SYS / 'System/Library/KernelCollections'
    kc.mkdir(exist_ok=True)
    for n in ('BootKernelExtensions.kc', 'BootKernelExtensions.kc.elides') + (('SystemKernelExtensions.kc',) if STAGE == 'a0' else ()):
        (kc / n).unlink(missing_ok=True)
        run('ditto', mkc / n, kc / n)
    if STAGE == 'a0':
        run('cp', '-c', kc / 'SystemKernelExtensions.kc', kc / 'BaseSystemKernelExtensions.kc')
    cs = BS / 'System/Library/CoreServices'
    for n in ('boot.efi', 'PlatformSupport.plist'):
        (cs / n).unlink(missing_ok=True)
        run('ditto', grp / 'System/Library/CoreServices' / n, cs / n)
    (cs / 'bootbase.efi').unlink(missing_ok=True)
    run('cp', '-c', cs / 'boot.efi', cs / 'bootbase.efi')
    dk = OS / 'System/DriverKit/System/Library/dyld'
    if dk.is_dir():
        (BS / 'System/DriverKit/System/Library/dyld').mkdir(parents=True, exist_ok=True)
        for p in dk.iterdir():
            if 'x86_64' in p.name:
                run('ditto', p, BS / 'System/DriverKit/System/Library/dyld' / p.name, quiet=True)
    dy = BS / 'System/Library/dyld'
    x86 = sorted(p for p in (OS / 'System/Library/dyld').iterdir() if p.name.startswith('dyld_shared_cache_x86_64'))
    record['x86_64_cache'] = [dict(name=p.name, size=p.stat().st_size) for p in x86]
    if STAGE == 'a0':
        for p in x86:
            run('ditto', p, dy / p.name, quiet=True)
        record['trust'] = trust_basesystem(BS, lists['no_counterpart'] + lists['differs'] + lists['no_x86_64'])
        log('trust: %d cdhashes added, %d slices refused' % (record['trust']['added'], len(record['trust']['refused'])))
    else:
        record['trust'] = merge_trust(BS, SHARE / 'in/hao-basesystem')
        log('trust: %s' % record['trust'])
        step('main-files', t)
        t = time.monotonic()
        record['kc'] = link_basesystem_kc(BS, SYS, TKC, SHARE / 'in/kmutil')
        log('BaseSystem KC: %d requested, %d members, %d bytes, missing %s' % (
            record['kc']['requested'], record['kc']['members'], record['kc']['bytes'], record['kc']['missing']))
        keep_ids = set(record['kc'].pop('member_ids')) | set(kc_members(kc / 'BootKernelExtensions.kc'))
        record['extensions'] = prune_extensions(BS, keep_ids, strip=record['tahoe_extensions']['with_executable'] == 0)
        log('/S/L/E: %s' % record['extensions'])
        step('kc', t)
        t = time.monotonic()
        record['cache'] = trim_cache(OS / 'System/Library/dyld', W / 'dsc', BS, SHARE / 'keep-extra.txt')
        for n in sorted(os.listdir(W / 'dsc')):
            run('ditto', W / 'dsc' / n, dy / n, quiet=True)
        run('rm', '-rf', W / 'dsc')
        log('x86_64 cache: %s' % {k: v for k, v in record['cache'].items() if k != 'files'})
        step('cache', t)
        t = time.monotonic()
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
    if STAGE == 'a1':
        record['budget'] = budget(BS)
        log('budget: %s' % record['budget'])
        for m in (BPB, BS):
            run('sync'); run('umount', m); mounted.remove(m)
        run('hdiutil', 'detach', rwwhole); attached.remove(rwwhole)
        out_a1(RW)
        step('out', t)
        return
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


def budget(bs):
    """Bytes per part of the finished volume: du (allocated, APFS compression counted) and, for the x86_64 cache,
    its logical size."""
    def du(*rel):
        ps = [str(bs / r) for r in rel if (bs / r).exists()]
        return sum(int(l.split()[0]) * 1024 for l in run('du', '-sk', *ps, quiet=True).splitlines()) if ps else 0
    dy = bs / 'System/Library/dyld'
    x86 = [p for p in dy.iterdir() if p.name.startswith('dyld_shared_cache_x86_64')]
    arm = [p.relative_to(bs) for p in dy.iterdir() if p.name.startswith('dyld_shared_cache_arm64e')]
    return dict(used=shutil.disk_usage(bs).used, x86_64_cache_logical=sum(p.stat().st_size for p in x86),
                x86_64_cache_du=du(*[p.relative_to(bs) for p in x86]), arm64e_cache_du=du(*arm),
                extensions_du=du('System/Library/Extensions'), kernel_collections_du=du('System/Library/KernelCollections'),
                driverkit_du=du('System/DriverKit'), hao_du=du('usr/lib/hao', 'usr/libexec/hao'),
                apps_du=du(*[p.relative_to(bs) for p in bs.glob('*.app')], 'System/Applications', 'Applications'),
                frameworks_du=du('System/Library/Frameworks', 'System/Library/PrivateFrameworks'),
                firmware_du=du('usr/standalone'))


def out_a1(rw):
    """A1 out: the sparse image compacted, BaseSystem.dmg (UDZO: zero chunks cost nothing, OpenCore reads zlib
    chunks), BaseSystem.chunklist (unsigned, pipeline/stages/chunklist.py: OpenCore checks chunk hashes, the signature
    only under DmgLoading Signed) and dmgdisk.img: a raw GPT disk whose one FAT32 partition holds
    com.apple.recovery.boot/ (the stick's and NetBoot's layout: FAT32 is where the 4 GiB file limit applies)."""
    W = rw.parent
    before = int(run('du', '-sk', rw, quiet=True).split()[0]) * 1024
    run('hdiutil', 'compact', rw, check=False)
    record['sparse_bytes'] = dict(before_compact=before, after_compact=int(run('du', '-sk', rw, quiet=True).split()[0]) * 1024)
    for n in ('BaseSystem.dmg', 'BaseSystem.chunklist', 'dmgdisk.img'):
        (OUT / n).unlink(missing_ok=True)
    t = time.monotonic()
    run('hdiutil', 'convert', rw, '-format', 'UDZO', '-o', W / 'BaseSystem.dmg', quiet=True)
    record['udzo_seconds'] = round(time.monotonic() - t, 1)
    os.unlink(rw)
    dsz = (W / 'BaseSystem.dmg').stat().st_size
    record['dmg_bytes'] = dsz
    log('BaseSystem.dmg UDZO %d bytes (%.2f GiB) in %s s' % (dsz, dsz / 2**30, record['udzo_seconds']))
    if dsz >= 4 * 2**30:
        raise RuntimeError('BaseSystem.dmg is %d bytes, not under 4 GiB' % dsz)
    run('hdiutil', 'verify', W / 'BaseSystem.dmg', quiet=True)
    run(sys.executable, '-I', SHARE / 'chunklist.py', 'create', W / 'BaseSystem.dmg', W / 'BaseSystem.chunklist')
    mb = dsz // 2**20 * 102 // 100 + 256
    run('hdiutil', 'create', '-size', '%dm' % mb, '-fs', 'MS-DOS FAT32', '-volname', 'GGA1', '-layout', 'GPTSPUD',
        W / 'dmgdisk.dmg')
    hwhole, ents = attach(W / 'dmgdisk.dmg')
    for e in ents:   # hdiutil adds an EFI system partition in front past ~2 GB (netrec.sh removes it the same way)
        if e.get('content-hint') in ('EFI', 'C12A7328-F81F-11D2-BA4B-00A0C93EC93B'):
            run('diskutil', 'eraseVolume', 'Free Space', '%noformat%', e['dev-entry'])
    fat = next(e['dev-entry'] for e in ents if e.get('content-hint') in ('Microsoft Basic Data', 'EBD0A0A2-B9E5-4433-87C0-68B6B72699C7'))
    H = W / 'fat'; H.mkdir()
    run('diskutil', 'mount', '-mountOptions', 'nobrowse', '-mountPoint', H, fat); mounted.append(H)
    (H / 'com.apple.recovery.boot').mkdir()
    for n in ('BaseSystem.dmg', 'BaseSystem.chunklist'):
        run('ditto', '--norsrc', '--noextattr', '--noacl', W / n, H / 'com.apple.recovery.boot' / n)
    run('cmp', W / 'BaseSystem.dmg', H / 'com.apple.recovery.boot/BaseSystem.dmg')
    run('find', H, '-name', '._*', '-delete')
    run('diskutil', 'unmount', H); mounted.remove(H)
    run('hdiutil', 'detach', hwhole); attached.remove(hwhole)
    run('hdiutil', 'convert', W / 'dmgdisk.dmg', '-format', 'UDTO', '-o', W / 'dmgdisk', quiet=True)
    os.unlink(W / 'dmgdisk.dmg')
    for src, n in ((W / 'BaseSystem.dmg', 'BaseSystem.dmg'), (W / 'BaseSystem.chunklist', 'BaseSystem.chunklist'),
                   (W / 'dmgdisk.cdr', 'dmgdisk.img')):
        shutil.copyfile(src, OUT / n); os.unlink(src)


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
