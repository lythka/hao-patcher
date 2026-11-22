"""Writing the USB stick (hao create-installer, step 3 "Write the USB stick"; workflow plan step 17).

The stick has the layout of the NetBoot Recovery (pipeline/stages/netrec.sh), on a USB disk, plus the kit:

  EFI        the EFI system partition diskutil makes on every GPT disk: the target's OpenCore exactly as OCLP installs
             it on an ESP (the ESP row of targets/<model>.json: EFI/OC + System/Library/CoreServices/boot.efi, OpenCore's
             Bootstrap, which a Mac's firmware starts), the Bootstrap also as EFI/BOOT/BOOTX64.EFI, the config changed as
             netrec.sh changes it (DmgLoading Any, BlessOverride to the Recovery folder, amfi=0x80, RequestBootVarRouting
             off, ...), its picker shown at once with the Recovery as the only entry, logging to a file on request
  HAOBOOT    FAT32 (Microsoft Basic Data): com.apple.recovery.boot/BaseSystem.dmg = the Recovery OpenCore loads
  HAO        Mac OS Extended (Journaled): `hao/` (this hao folder without its store), `kit/` (golden-run --stage-kit),
             `install` and `report` (launchers for the Recovery's Terminal, run with the kit's own CPython),
             `release.json` when there is one, `stick.json` (what was written, with sha256s), `reports/`

The Recovery is the Tahoe BaseSystem, as DIRECTION's "Recovery on a stick" says until the Golden Gate Recovery is
proven; which BaseSystem.dmg (stock, or the end-user Recovery `pipeline/recovery` builds) is the caller's choice.
Nothing here formats APFS. A dev host writes a stick image file (`--image FILE`): a raw disk attached with hdiutil,
written exactly as a stick, then detached.

    python3 stick.py opencore-config IN.plist OUT.plist      the config change alone (tests compare it with netrec.sh)
    sudo python3 stick.py rewrite-boot diskN [--opencore-log]  only EFI and HAOBOOT of a USB installer, again
"""
import datetime
import hashlib
import json
import os
from pathlib import Path
import plistlib
import shlex
import shutil
import stat
import subprocess
import sys
import tarfile

BOOT_NAME, KIT_NAME = 'HAOBOOT', 'HAO'
MB = 1024 * 1024
# FAT32 tables, clusters and GPT on top of the BaseSystem and OpenCore (netrec.sh: 2 % + 256 MB; less was too tight)
SLACK_MB = 256
SCAN_POLICY = 0x00000801            # FILE_SYSTEM_LOCK | FS_NTFS (Basic Data partitions), no DEVICE_LOCK
LAUNCHER = '''#!/bin/sh
# %(what)s, from the stick: hao %(cmd)s with the kit's own CPython (the Recovery has no Python).
D=$(cd "$(dirname "$0")" && pwd)
export HAO_STICK=$D
case $(uname -m) in x86_64) PY=$D/kit/py-x86_64 ;; *) PY=$D/kit/py ;; esac
# Run that CPython from memory (64 MB): the install first reads the whole drive to check it, and a page of a running
# program read back from a USB drive under that load can fail (SIGBUS). Where /private/var/tmp is not writable, as is.
M=/private/var/tmp/hao-py.$$
if [ -w /private/var/tmp ] && ditto "$PY" "$M" 2>/dev/null; then PY=$M; fi
export PYTHONDONTWRITEBYTECODE=1
exec "$PY/bin/python3.12" -I -B "$D/hao/tools/hao/hao" %(cmd)s %(args)s"$@"
'''
LAUNCHERS = {
    'install': dict(what='Install macOS 27', cmd='install', args='--kit "$D/kit" %(assembly)s%(unproven)s'),
    'report': dict(what='A report for a GitHub issue', cmd='report', args='--output "$D/reports/hao-report-$(date +%Y%m%d-%H%M%S).txt" '),
}
SKIP = {'store', '.git', '__pycache__', '.DS_Store', '.claude'}


class StickError(Exception):
    pass


def is_generic_config(c):
    """The generic layout's config (VMs, PCs): it loads VirtualSMC, which a Mac's OCLP config never does (a Mac has an SMC)."""
    return any(k.get('BundlePath') == 'VirtualSMC.kext' and k.get('Enabled') for k in c['Kernel']['Add'])


def generic_args(c):
    """-lilubetaall for the generic config: without it the Tahoe Recovery's GUI never draws in QEMU with OCLP's Lilu 1.7.2
    and VirtualSMC (with it, the Language Chooser does: the generic USB installer's A/B, 2026-11-17)."""
    return ['-lilubetaall'] if is_generic_config(c) else []


IDENTITY_ALPHABET = '0123456789ABCDEFGHJKLMNPQRSTUVWXYZ'
SERIAL_YEARS = 'CDFGHJKLMNPQRSTVWXYZ'          # 4th character of a 12-character serial
SERIAL_WEEKS = '123456789CDFGHJKLMNPQRTVWXY'   # 5th


def generic_identity(c):
    """A generic config whose machine identity is blank (the tracked base config carries none) gets a random one, made
    here for this USB installer: serial, board serial (MLB), system UUID, ROM. Random, never a real Mac's; the values
    are returned in the config and so recorded with the USB installer's ESP. A Mac's config is left alone."""
    if not is_generic_config(c):
        return None
    import secrets, uuid
    g = c.setdefault('PlatformInfo', {}).setdefault('Generic', {})
    if g.get('SystemSerialNumber') and g.get('SystemUUID'):
        return None
    pick = lambda n, alphabet=IDENTITY_ALPHABET: ''.join(secrets.choice(alphabet) for _ in range(n))
    # laid out as Apple's 12-character serials are (plant, year, week, unit, model): macOS reads the year and week from
    # it, and a serial whose year or week is not one of them left the Recovery's CPU power management stalled (QEMU)
    serial = pick(3) + pick(1, SERIAL_YEARS) + pick(1, SERIAL_WEEKS) + pick(7)
    g.update(SystemSerialNumber=serial, MLB=pick(17), SystemUUID=str(uuid.uuid4()).upper(), ROM=secrets.token_bytes(6))
    return g


def opencore_config(c):
    """The stick's OpenCore config from the target's (a plist dict, changed in place and returned): netrec.sh's
    changes, so the stick boots the unsigned BaseSystem.dmg the way the NetBoot Recovery does."""
    sec, boot = c['Misc']['Security'], c['Misc']['Boot']
    sec['DmgLoading'] = 'Any'
    sec['ScanPolicy'] = SCAN_POLICY
    c['Misc']['BlessOverride'] = ['\\com.apple.recovery.boot\\']
    boot['LauncherOption'] = 'Disabled'
    boot['HideAuxiliary'] = False
    c['UEFI']['Quirks']['RequestBootVarRouting'] = False
    for k in c['Kernel']['Add']:
        if k['BundlePath'] == 'AutoPkgInstaller.kext':
            k['Enabled'] = False
    nv = c['NVRAM']['Add']['7C436110-AB2A-4BBB-A880-FE41995C9F82']
    args = nv['boot-args'].split()
    nv['boot-args'] = ' '.join(args + [a for a in ['amfi=0x80'] + generic_args(c) if a not in args])
    generic_identity(c)
    rev = c['NVRAM']['Add'].get('4D1FDA02-38C7-4A6A-9CC6-4BCCA8B30102', {})
    if 'revpatch' in rev:
        rev['revpatch'] = ','.join(x for x in rev['revpatch'].split(',') if x != 'sbvmm')
    ias = '4D1EDE05-38C7-4A6A-9CC6-4BCCA8B38C14'
    for k in ('IASInstallPhaseList', 'IASCurrentInstallPhase', 'DefaultBackgroundColor'):
        c['NVRAM']['Add'].get(ias, {}).pop(k, None)
        if k in c['NVRAM'].get('Delete', {}).get(ias, []) and k != 'DefaultBackgroundColor':
            c['NVRAM']['Delete'][ias].remove(k)
    c['Kernel']['Patch'].append({
        'Arch': 'x86_64', 'Base': '', 'Comment': 'netrec: even boot progress bar (vc_progressmeter_task incr[])',
        'Count': 1, 'Enabled': True, 'Identifier': 'kernel', 'Limit': 0, 'Mask': b'', 'ReplaceMask': b'',
        'MinKernel': '25.0.0', 'MaxKernel': '25.99.99', 'Skip': 0,
        'Find': bytes.fromhex('10271027002000100008800180014000'), 'Replace': (65535).to_bytes(2, 'little') * 8})
    return c


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        try:                    # F_NOCACHE: streaming the drive does not push everything else out of memory
            __import__('fcntl').fcntl(f.fileno(), 48, 1)
        except (OSError, ImportError):
            pass
        for block in iter(lambda: f.read(8 * MB), b''):
            h.update(block)
    return h.hexdigest()


def tree_bytes(path):
    path = Path(path)
    if path.is_file():
        return path.stat().st_size
    return sum(p.lstat().st_size for p in path.rglob('*') if p.is_file() and not p.is_symlink())


def kit_bytes(path):
    """Bytes a tree takes, each hard-linked file once."""
    seen, total = set(), 0
    for p in Path(path).rglob('*'):
        st = p.lstat()
        if p.is_file() and not p.is_symlink() and (st.st_dev, st.st_ino) not in seen:
            seen.add((st.st_dev, st.st_ino))
            total += st.st_size
    return total


def boot_mb(basesystem_bytes, opencore_bytes):
    """HAOBOOT's size in MB: netrec.sh's formula."""
    return basesystem_bytes // MB * 102 // 100 + opencore_bytes // MB + SLACK_MB


# OpenCore can write its log (opencore-DATE.txt) to the root of its volume, the USB installer's EFI partition: 0x41 =
# enabled + to file. Off unless asked for (LOG=True): OpenCore writes the file line by line through the Mac's firmware,
# which on a USB drive lengthens the pause after "EFI Boot". The log is how a boot that shows nothing is timed afterwards.
LOG_TARGET = 0x41
# What the Mac shows after "EFI Boot": OpenCore's picker at once (the Recovery its only entry: ScanPolicy allows Basic
# Data partitions only; OCLP's tools and the Reset NVRAM entry are off), which starts the Recovery after PICKER_TIMEOUT
# seconds or on Return.
# Without the picker (ShowPicker false, Timeout 0) Apple's boot picker stayed on screen, frozen, while OpenCore read
# the Recovery (owner, laptop 1, 2026-11-16).
PICKER_TIMEOUT = 5
RECOVERY_LABEL = 'hao Installer'


def opencore_files(tar_path, esp, log=False):
    """The ESP row's tree, as OCLP installs it, onto the stick's EFI partition ESP: EFI/OC (OpenCore.efi, config.plist,
    drivers, kexts, resources), System/Library/CoreServices/boot.efi (OpenCore's Bootstrap: what a Mac's firmware
    starts from an EFI partition, and what then loads EFI/OC/OpenCore.efi), .VolumeIcon.icns; plus the same Bootstrap
    as EFI/BOOT/BOOTX64.EFI (the removable-media path). The config is changed as netrec.sh changes it, and logs to
    a file only when LOG. Starting OpenCore.efi itself as BOOTX64.EFI from a Basic Data partition, as the first sticks did, left the
    Mac's boot picker hanging (owner, two MacBookPro9,2, 2026-11-13); that layout only ever ran under iPXE."""
    esp = Path(esp)
    with tarfile.open(tar_path) as t:
        members = [m for m in t.getmembers() if not os.path.isabs(m.name) and '..' not in Path(m.name).parts]
        for m in members:
            rel = Path(m.name.lstrip('./'))
            if not rel.parts:
                continue
            out = esp / rel
            if m.isdir():
                out.mkdir(parents=True, exist_ok=True)
            elif m.isfile():
                out.parent.mkdir(parents=True, exist_ok=True)
                with t.extractfile(m) as src, open(out, 'wb') as f:
                    shutil.copyfileobj(src, f)
    oc, bootstrap = esp / 'EFI' / 'OC', esp / 'System' / 'Library' / 'CoreServices' / 'boot.efi'
    removable = esp / 'EFI' / 'BOOT' / 'BOOTX64.EFI'
    for need in (oc / 'OpenCore.efi', oc / 'config.plist'):
        if not need.is_file():
            raise StickError('%s has no %s' % (tar_path, need.relative_to(esp)))
    if bootstrap.is_file():                 # a Mac's layout (OCLP): the Bootstrap also on the removable-media path
        removable.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(bootstrap, removable)
    elif not _found(esp / 'EFI' / 'BOOT', 'BOOTX64.EFI'):   # the generic layout (VMs, PCs) has only EFI/BOOT/BOOTx64.efi
        raise StickError('%s has no Bootstrap (System/Library/CoreServices/boot.efi or EFI/BOOT/BOOTx64.efi)' % tar_path)
    p = oc / 'config.plist'
    c = opencore_config(plistlib.loads(p.read_bytes()))
    c['Misc']['Boot'].update(ShowPicker=True, Timeout=PICKER_TIMEOUT)
    for tool in c['Misc'].get('Tools', []):        # the Recovery is the one entry (HideAuxiliary cannot do it: OpenCore
        tool['Enabled'] = False                    # counts a Recovery as auxiliary too, and then shows every entry)
    for driver in c['UEFI'].get('Drivers', []):
        if str(driver.get('Path', '')).endswith('ResetNvramEntry.efi'):
            driver['Enabled'] = False
    c['Misc'].setdefault('Debug', {})['Target'] = LOG_TARGET if log else 0
    p.write_bytes(plistlib.dumps(c))
    return c


DEDUPE_BYTES = 64 * MB
# Kit paths the stick leaves out. cryptex-cache/in holds sealed cryptex images keyed on the Mac that made them
# (its macOS build and newfs_apfs, e.g. the builder VM's 25G83): a stick's Recovery (25G342, x86_64) never matches
# that key and makes its own, so the 24 GB would only fill the stick. key.json stays.
KIT_LEFT_OUT = ('cryptex-cache/in',)


def hao_files(top):
    """The hao folder's files, relative: in a git checkout the tracked ones (so nothing git ignores, such as local
    notes, reaches a stick), in a release archive every file but the store and caches."""
    top = Path(top)
    if (top / '.git').exists():
        r = subprocess.run(['git', '-c', 'safe.directory=%s' % top, '-C', str(top), 'ls-files', '-z'],  # a user's
                           stdout=subprocess.PIPE, check=True)                         # checkout, under sudo
        out = [f for f in r.stdout.decode().split('\0') if f and ((top / f).exists() or (top / f).is_symlink())]
        # a candidate build's key (never tracked: release-cut --candidate writes it) goes with hao, so the drive's
        # hao install accepts the candidate-signed release.json it carries
        cand = 'tools/hao/data/candidate-keys.pub'
        if (top / cand).is_file() and not (top / cand).is_symlink():
            out.append(cand)
        return sorted(set(out))
    out = []
    for root, dirs, files in os.walk(top):
        dirs[:] = sorted(d for d in dirs if d not in SKIP)
        out += [str((Path(root) / f).relative_to(top)) for f in sorted(files) if f not in SKIP and not f.startswith('._')]
    return out


def copy_hao(top, dest):
    top, dest = Path(top), Path(dest)
    for rel in hao_files(top):
        src, out = top / rel, dest / rel
        if not rel or rel.split('/', 1)[0] in SKIP:
            continue
        out.parent.mkdir(parents=True, exist_ok=True)
        if src.is_symlink():
            os.symlink(os.readlink(src), out)
        elif src.is_file():
            shutil.copy2(src, out)


def copy_tree(src, dest, skip=SKIP, linked=None, skip_paths=()):
    """SRC into DEST, without the store, git and caches; symlinks kept as links. A file of DEDUPE_BYTES or more whose
    bytes equal one already copied (same size and name, then same sha256) becomes a hard link to it: the kit carries
    the Golden Gate userspace payload in two hooks, APFS clones in the store, 22 GB twice on the stick otherwise.
    LINKED (a list) gets (copy, linked to) for each."""
    src, dest = Path(src), Path(dest)
    seen = {}
    for root, dirs, files in os.walk(src):
        r = Path(root)
        dirs[:] = sorted(d for d in dirs if d not in skip and str((r / d).relative_to(src)) not in skip_paths)
        out = dest / r.relative_to(src)
        out.mkdir(parents=True, exist_ok=True)
        for d in list(dirs):
            if (r / d).is_symlink():
                dirs.remove(d)
                os.symlink(os.readlink(r / d), out / d)
        for f in sorted(files):
            if f in skip or f.startswith('._'):
                continue
            if (r / f).is_symlink():
                os.symlink(os.readlink(r / f), out / f)
                continue
            size = (r / f).stat().st_size
            if size >= DEDUPE_BYTES:
                twins = seen.setdefault((size, f), [])
                digest = None
                for first, first_digest in twins:
                    digest = digest or sha256(r / f)
                    if digest == first_digest:
                        os.link(first, out / f)
                        if linked is not None:
                            linked.append((out / f, first))
                        break
                else:
                    shutil.copy2(r / f, out / f)
                    twins.append((out / f, digest or sha256(out / f)))
                continue
            shutil.copy2(r / f, out / f)


def write_new(path, data, mode=0o644):
    """DATA (bytes) as PATH, never through a link: an exclusive, no-follow temporary file in PATH's folder (which must
    be a real folder), then renamed over PATH (a rename replaces a link at PATH, it does not follow it). For volumes
    whose existing contents are not trusted (an existing USB installer's HAO)."""
    path = Path(path)
    try:            # the folder opened once, without following a link: the file and the rename are relative to it
        dfd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError:
        raise StickError('%s is not a folder (a link?)' % path.parent) from None
    tmp = '.%s.%s.tmp' % (path.name, os.urandom(6).hex())
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode, dir_fd=dfd)
        try:
            with os.fdopen(fd, 'wb') as f:
                f.write(data)
            os.replace(tmp, path.name, src_dir_fd=dfd, dst_dir_fd=dfd)
        except BaseException:
            try:
                os.unlink(tmp, dir_fd=dfd)
            except OSError:
                pass
            raise
    finally:
        os.close(dfd)


def read_plain(path):
    """PATH's bytes, refusing a link (no-follow open) or anything but a regular file."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as x:
        raise StickError('%s: not read (%s)' % (path, x.strerror)) from None
    with os.fdopen(fd, 'rb') as f:
        if not __import__('stat').S_ISREG(os.fstat(f.fileno()).st_mode):
            raise StickError('%s is not a regular file' % path)
        try:
            return f.read()
        except OSError as x:
            raise StickError('%s: not read (%s)' % (path, x.strerror)) from None


def real_folder(path):
    """PATH as a real folder (made when missing); a link or a file there is refused."""
    path = Path(path)
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        path.mkdir()
        return path
    if not __import__('stat').S_ISDIR(st.st_mode):
        raise StickError('%s is not a folder (a link?): refusing to write through it' % path)
    return path


MANIFEST = 'manifest.json'
# On HAO but not in the manifest: what macOS and the user add (reports), and the manifest itself
MANIFEST_SKIP = {MANIFEST, 'reports', 'logs', '.fseventsd', '.Spotlight-V100', '.Trashes', '.TemporaryItems',
                 '.DS_Store', '.DocumentRevisions-V100', '.VolumeIcon.icns', '.hao-build'}
WORK_PREFIX = 'hao-install-'        # hao install's work folders on HAO (written after the check; rebuilt from the kit)


def _skipped_top(name):
    return name in MANIFEST_SKIP or name.startswith(WORK_PREFIX)
CUSTODY = ('Keep the USB installer with you between making it and installing: anyone who can change it can change '
           'what it installs. hao install checks it against its manifest first, which catches damage and casual edits.')


def _manifest_walk(top):
    """(relative path, os.stat_result, kind) of everything on HAO the manifest covers, without following links. Only
    a few top-level names are left out (macOS's own folders, reports, logs, install work); inside hao/ and kit/ only
    Finder's .DS_Store is. A folder that cannot be read is an error, not a gap."""
    top = Path(top)

    def unreadable(err):
        raise StickError('%s: cannot be read (%s)' % (err.filename, err.strerror))
    for root, dirs, files in os.walk(top, onerror=unreadable):
        r = Path(root)
        rel_root = r.relative_to(top)
        if rel_root == Path('.'):
            dirs[:] = [d for d in dirs if not _skipped_top(d)]
        dirs.sort()
        for name in sorted(files) + [d for d in dirs if (r / d).is_symlink()]:
            if (rel_root == Path('.') and _skipped_top(name)) or (name == '.DS_Store' and _finder_file(r / name)):
                continue
            p = r / name
            st = os.lstat(p)
            kind = 'link' if __import__('stat').S_ISLNK(st.st_mode) else 'file' if __import__('stat').S_ISREG(
                st.st_mode) else 'other'
            yield str(p.relative_to(top)), st, kind
        dirs[:] = [d for d in dirs if not (r / d).is_symlink()]


def _finder_file(path):
    """A real Finder .DS_Store (a small regular file with its header), the one name left out inside hao/ and kit/."""
    try:
        st = os.lstat(path)
        if not __import__('stat').S_ISREG(st.st_mode) or st.st_size > 1 << 20:
            return False
        with open(path, 'rb') as f:
            return f.read(8) == b'\x00\x00\x00\x01Bud1'
    except OSError:
        return False


def _link_ok(top, rel, target):
    """A link on HAO may not lead into what the manifest leaves out (reports, logs, install work, the manifest)."""
    if os.path.isabs(target):
        return
    inside = os.path.normpath(os.path.join(os.path.dirname(rel), target))
    first = inside.split(os.sep, 1)[0]
    if inside != '.' and not inside.startswith('..') and _skipped_top(first):
        raise StickError('%s links into %s, which the manifest does not cover' % (rel, first))


def write_manifest(top, on_file=None):
    """HAO/manifest.json: every file on HAO (hao, the kit, the launchers, release.json, stick.json) with its sha256,
    size and mode, and every link with its target, read back from the drive. hao install checks the drive against it
    before it erases anything (O-1: custody plus an integrity check). Returns the manifest."""
    top = Path(top)
    files, links = {}, {}
    seen, unique = {}, 0
    for rel, st, kind in _manifest_walk(top):
        if kind == 'link':
            links[rel] = os.readlink(top / rel)
            _link_ok(top, rel, links[rel])
        elif kind == 'file':
            key = (st.st_dev, st.st_ino)
            if key not in seen:                             # hard links (the userspace twin) hashed and counted once
                seen[key] = sha256(top / rel)
                unique += st.st_size
            digest = seen[key]
            files[rel] = {'sha256': digest, 'size': st.st_size, 'mode': '%04o' % (st.st_mode & 0o7777)}
            if on_file:
                on_file(rel, st.st_size)
        else:
            raise StickError('%s on %s is neither a file nor a link' % (rel, KIT_NAME))
    m = {'format': 1, 'files': files, 'links': links, 'release_sha256': files.get('release.json', {}).get('sha256'),
         'bytes': unique}
    write_new(top / MANIFEST, (json.dumps(m, indent=1, sort_keys=True) + '\n').encode())
    return m


def check_manifest(top, on_file=None, on_start=None):
    """What on HAO differs from its manifest: [(path, problem)], empty when the drive is as it was written. Every
    listed file is read and hashed; a file or link that is not listed is a problem too. ON_START(rel, size) is called
    before each file is read (a trace names the file a crash happened in)."""
    top = Path(top)
    try:
        m = json.loads(read_plain(top / MANIFEST))
    except (StickError, ValueError) as x:
        return [(MANIFEST, 'missing or unreadable (%s)' % x)]
    if not _manifest_shape(m):
        return [(MANIFEST, 'not a format-1 manifest')]
    try:
        return _compare(top, m, on_file, on_start)
    except (StickError, OSError) as x:
        return [('(drive)', 'could not be read completely: %s' % x)]


def _manifest_shape(m):
    if not isinstance(m, dict) or m.get('format') != 1 or not isinstance(m.get('files'), dict) or \
            not isinstance(m.get('links'), dict):
        return False
    for v in m['files'].values():
        if not (isinstance(v, dict) and isinstance(v.get('sha256'), str) and len(v['sha256']) == 64 and
                isinstance(v.get('size'), int) and isinstance(v.get('mode'), str)):
            return False
    return all(isinstance(v, str) for v in m['links'].values())


def _compare(top, m, on_file, on_start=None):
    problems, found, hashed = [], set(), {}
    for rel, st, kind in _manifest_walk(top):
        found.add(rel)
        if kind == 'link':
            if rel not in m['links']:
                problems.append((rel, 'a link that is not in the manifest'))
            elif os.readlink(top / rel) != m['links'][rel]:
                problems.append((rel, 'the link points elsewhere'))
            else:
                try:
                    _link_ok(top, rel, m['links'][rel])
                except StickError as x:
                    problems.append((rel, str(x)))
        elif kind == 'file':
            want = m['files'].get(rel)
            if want is None:
                problems.append((rel, 'a file that is not in the manifest'))
            elif st.st_size != want['size']:
                problems.append((rel, 'a different size'))
            elif '%04o' % (st.st_mode & 0o7777) != want['mode']:
                problems.append((rel, 'a different mode'))
            else:
                key = (st.st_dev, st.st_ino)            # hard links (the userspace twin) are read once
                first = key not in hashed
                if first:
                    if on_start:
                        on_start(rel, st.st_size)
                    hashed[key] = sha256(top / rel)
                if hashed[key] != want['sha256']:
                    problems.append((rel, 'different content (sha256)'))
                if on_file and first:
                    on_file(rel, st.st_size)
        else:
            problems.append((rel, 'neither a file nor a link'))
    for rel in sorted(set(m['files']) | set(m['links'])):
        if rel not in found:
            problems.append((rel, 'missing'))
    return problems


def verify_kit(kit, copy, skip=('.DS_Store', '__pycache__'), skip_paths=KIT_LEFT_OUT):
    """Every regular file of the staged KIT is in COPY (the kit on the drive) with the same size, apart from what the
    drive leaves out; symlinks (moved in from the drive, or kept as links) are not compared. Returns the files checked;
    raises StickError naming what is missing."""
    kit, copy = Path(kit), Path(copy)
    checked, bad = 0, []
    for root, dirs, files in os.walk(kit):
        r = Path(root)
        dirs[:] = [d for d in dirs if d not in skip and str((r / d).relative_to(kit)) not in skip_paths
                   and not (r / d).is_symlink()]
        for f in files:
            p = r / f
            if f in skip or f.startswith('._') or p.is_symlink():
                continue
            q = copy / p.relative_to(kit)
            if not q.is_file() or q.stat().st_size != p.stat().st_size:
                bad.append(str(p.relative_to(kit)))
            checked += 1
    if bad:
        raise StickError('the kit on the drive differs from the staged one: %d files (%s)'
                         % (len(bad), ', '.join(bad[:3])))
    return checked


def _found(folder, name):
    """FOLDER's entry named NAME in any case (an ESP is FAT: BOOTx64.efi and BOOTX64.EFI are one file), or None."""
    try:
        return next((p for p in Path(folder).iterdir() if p.name.upper() == name.upper()), None)
    except OSError:
        return None


def write_boot(esp, mount, basesystem, opencore_tar, log=False):
    """OpenCore onto the EFI partition ESP, the Recovery onto HAOBOOT (MOUNT; not copied again when the same
    BaseSystem.dmg is already there). Returns what stick.json records."""
    esp, mount = Path(esp), Path(mount)
    c = opencore_files(opencore_tar, esp, log=log)
    for p in esp.rglob('._*'):
        p.unlink()
    rec = mount / 'com.apple.recovery.boot'
    rec.mkdir(parents=True, exist_ok=True)
    want, dest = sha256(basesystem), rec / 'BaseSystem.dmg'
    same = dest.is_file() and (dest.resolve() == Path(basesystem).resolve() or (
        dest.stat().st_size == Path(basesystem).stat().st_size and sha256(dest) == want))
    if not same:
        shutil.copyfile(basesystem, dest)
    (rec / '.contentDetails').write_text(RECOVERY_LABEL)
    for p in mount.rglob('._*'):
        p.unlink()
    got = sha256(dest)
    if want != got:
        raise StickError('BaseSystem.dmg on the USB installer has sha256 %s, not %s' % (got, want))
    nv = c['NVRAM']['Add']['7C436110-AB2A-4BBB-A880-FE41995C9F82']
    return dict(basesystem_sha256=want, basesystem_bytes=Path(basesystem).stat().st_size,
                opencore_sha256=sha256(opencore_tar), boot_args=nv['boot-args'],
                config_sha256=sha256(esp / 'EFI' / 'OC' / 'config.plist'),
                bootstrap_sha256=sha256(_found(esp / 'EFI' / 'BOOT', 'BOOTX64.EFI')),
                opencore_log=c['Misc']['Debug']['Target'], opencore_picker_timeout=PICKER_TIMEOUT)


BUILD_DIR = '.hao-build'     # on HAO: what create-installer makes straight onto the drive (the userspace images)


def adopt_drive_links(kit, mount, moved=None):
    """Every symlink in KIT (on the drive MOUNT) that points into MOUNT/BUILD_DIR becomes the folder or file it points
    to: moved there the first time (a rename on the same volume, no copy), hard-linked file by file after that (the
    userspace images are in two hooks). Returns [(link, target)]."""
    kit, base = Path(kit), (Path(mount) / BUILD_DIR).resolve()
    moved = {} if moved is None else moved
    done = []
    for link in sorted(p for p in kit.rglob('*') if p.is_symlink()):
        target = Path(os.path.realpath(link))
        if target != base and base not in target.parents and str(target) not in moved:
            continue
        link.unlink()
        if str(target) not in moved:
            if not target.exists():
                raise StickError('%s points to %s, which is not on the drive' % (link, target))
            os.rename(target, link)
            moved[str(target)] = link
        else:
            first = Path(moved[str(target)])
            if first.is_dir():
                link.mkdir()
                for p in sorted(first.rglob('*')):
                    out = link / p.relative_to(first)
                    if p.is_dir() and not p.is_symlink():
                        out.mkdir(parents=True, exist_ok=True)
                    elif p.is_symlink():
                        os.symlink(os.readlink(p), out)
                    else:
                        os.link(p, out)
            else:
                os.link(first, link)
        done.append((link, target))
    return done


def write_kit(mount, kit, hao_top, release=None, record=None):
    """HAO's files: hao, the kit, the launchers, release.json and stick.json. Payloads already made on the drive
    (BUILD_DIR, linked from the kit) are moved into the kit, then BUILD_DIR is removed."""
    mount, kit = Path(mount), Path(kit)
    if not (kit / 'kit.json').is_file():
        raise StickError('%s is no kit (no kit.json)' % kit)
    copy_hao(hao_top, mount / 'hao')
    linked = []
    copy_tree(kit, mount / 'kit', skip={'.DS_Store', '__pycache__'}, linked=linked, skip_paths=KIT_LEFT_OUT)
    adopted = adopt_drive_links(mount / 'kit', mount)
    shutil.rmtree(mount / BUILD_DIR, ignore_errors=True)
    verified = verify_kit(kit, mount / 'kit')
    assembly = (record or {}).get('assembly')
    for name, fill in LAUNCHERS.items():
        p = mount / name
        fill = dict(fill, args=fill['args'] % {'assembly': '--assembly %s ' % shlex.quote(assembly) if assembly else '',
                                               'unproven': '--unproven ' if (record or {}).get('unproven') else ''}
                    if '%(assembly)s' in fill['args'] else fill['args'])
        p.write_text(LAUNCHER % fill)
        p.chmod(0o755)
    (mount / 'reports').mkdir(exist_ok=True)
    if release:
        shutil.copyfile(release, mount / 'release.json')
        sig = Path(str(release) + '.sig')          # its signature, checked again by hao install
        if sig.is_file():
            shutil.copyfile(sig, mount / 'release.json.sig')
        sums = Path(release).parent / 'SHA256SUMS'  # an unsigned release (v0.0.1) is checked against it
        if sums.is_file():
            shutil.copyfile(sums, mount / 'SHA256SUMS')
    rec = dict(record or {})
    rec.update(schema=1, kit_json_sha256=sha256(kit / 'kit.json'), kit_bytes=kit_bytes(mount / 'kit'),
               hard_links=[[str(Path(a).relative_to(mount)), str(Path(b).relative_to(mount))] for a, b in linked],
               made_on_drive=[str(Path(a).relative_to(mount)) for a, _ in adopted], kit_files_verified=verified,
               release_sha256=sha256(release) if release else None)
    prelinked = sorted(kit.glob('hooks/*darwin27-kernel/prelinked/prelinked.json'))
    rec['kernel_collections'] = (
        dict(source='prelinked', key=json.loads(prelinked[0].read_text()).get('key')) if prelinked else
        dict(source='linked by hao install on the target, as root (Tahoe kmutil + its x86_64 collection builder)'))
    write_new(mount / 'stick.json', (json.dumps(rec, indent=1, sort_keys=True) + '\n').encode())
    close_tree(mount)
    write_manifest(mount)
    return rec


def close_tree(top):
    """TOP (the HAO volume) and everything in it, links not followed: not writable by group or others. A new JHFS+
    volume's root is drwxrwxr-x, which the host shows as the user's (owners ignored) but the Recovery, with owners on,
    shows as root:wheel group-writable; hao install refuses a kit others can change (safety.others_can_change_tree)."""
    top = Path(top)
    os.chmod(top, 0o755)
    for here, dirs, files in os.walk(top):
        for name in dirs + files:
            p = os.path.join(here, name)
            st = os.lstat(p)
            if not stat.S_ISLNK(st.st_mode) and st.st_mode & 0o022:
                os.chmod(p, st.st_mode & 0o7755)


# -- the disk

def _plist(host, *argv):
    return plistlib.loads(host.run(list(argv), timeout=600).stdout)


def partition(host, disk, boot_size_mb, expect=None):
    """Erase DISK (a whole disk the caller checked and the user confirmed) into EFI + HAOBOOT (FAT32) + HAO (JHFS+).
    EXPECT (disks.identity at the confirmation, with its policy, minimum and sources) is checked again first: a disk that
    took the confirmed one's number is never erased. Returns {name: (device, mount point)}, EFI as 'EFI'."""
    if expect is not None:
        import disks as DISKS
        why = DISKS.recheck(host, disk, expect['identity'], expect.get('policy', 'external'),
                            expect.get('minimum', 0), expect.get('sources', ()))
        if why:
            raise StickError('/dev/%s was not erased: %s' % (disk, why))
    admin = as_admin(host, disk)
    host.run(admin + ['diskutil', 'unmountDisk', 'force', '/dev/' + disk], check=False)
    host.run(admin + ['diskutil', 'partitionDisk', '/dev/' + disk, 'GPT',
                      'MS-DOS', BOOT_NAME, '%dM' % boot_size_mb, 'JHFS+', KIT_NAME, 'R'], timeout=1200)
    parts = mounted_parts(host, disk, 'after partitioning %s' % disk, admin)
    if admin:
        # made by root: HAO is handed to the user running hao, who writes everything else on the drive
        for name, part in parts.items():
            if part[1] and not os.access(part[1], os.W_OK):
                host.run(admin + ['chown', str(os.getuid()), part[1]])
    return parts


def as_admin(host, disk):
    """The prefix for the few commands that need an administrator (erasing the drive): none as root or for a device this
    user can write already (a disk image it attached: --image), else sudo -n, which uses the password create-installer
    asked for (installer.administrator) just before."""
    euid = getattr(host, 'euid', None)
    if euid is None or euid() == 0 or own_device(disk):
        return []
    return ['sudo', '-n']


def own_device(disk):
    """Whether this user can read and write /dev/DISK (macOS gives an attached disk image's device to who attached it)."""
    return os.access('/dev/' + disk, os.R_OK | os.W_OK)


def check_mounts(host, disk, parts):
    """Each saved mount point of PARTS still belongs to DISK (the same partition): nothing is written into a volume
    that took the USB drive's place under /Volumes. Raises StickError."""
    for name, part in parts.items():
        dev, mount = part[0], part[1]
        uuid = part[2] if len(part) > 2 else None
        if not uuid:                    # without its volume UUID a partition cannot be told from one that took its place
            raise StickError('%s (%s) has no volume UUID: hao cannot check it is still the USB drive' % (mount, name))
        try:
            info = _plist(host, 'diskutil', 'info', '-plist', str(mount))
        except Exception:
            info = {}
        if info.get('DeviceIdentifier') != dev or info.get('ParentWholeDisk', disk) != disk or \
                (uuid and (info.get('VolumeUUID') or info.get('DiskUUID')) != uuid):
            raise StickError('%s (%s) is no longer /dev/%s on the USB drive: was it unplugged?' % (mount, name, dev))


def mounted_parts(host, disk, when=None, admin=None):
    """DISK's EFI, HAOBOOT and HAO partitions, each mounted: {name: (device, mount point)}. A partition that is not
    mounted is mounted through ADMIN (as_admin's prefix, found from DISK when not given): macOS mounts an EFI
    partition only for root, and the erase leaves it unmounted."""
    admin = as_admin(host, disk) if admin is None else admin
    found = {}
    for part in _plist(host, 'diskutil', 'list', '-plist', disk).get('AllDisksAndPartitions', [{}])[0].get('Partitions', []):
        name = 'EFI' if part.get('Content') == 'EFI' else part.get('VolumeName')
        if name in ('EFI', BOOT_NAME, KIT_NAME):
            info = _plist(host, 'diskutil', 'info', '-plist', part['DeviceIdentifier'])
            if not info.get('MountPoint'):
                host.run(admin + ['diskutil', 'mount', part['DeviceIdentifier']])
                info = _plist(host, 'diskutil', 'info', '-plist', part['DeviceIdentifier'])
            found[name] = (part['DeviceIdentifier'], info.get('MountPoint'), info.get('VolumeUUID') or info.get('DiskUUID'))
    missing = [n for n in ('EFI', BOOT_NAME, KIT_NAME) if not (found.get(n) or (None, None))[1]]
    if missing:
        raise StickError('%s, %s is not mounted' % (when or 'on %s' % disk, ', '.join(missing)))
    return found


def attach_image(host, image, size_bytes):
    """A new raw stick image IMAGE of SIZE_BYTES (sparse), attached without mounting. Returns its diskN."""
    image = Path(image)
    if image.exists():
        raise StickError('%s exists' % image)
    image.parent.mkdir(parents=True, exist_ok=True)
    with open(image, 'xb') as f:          # exclusive: never truncates a file or follows a link put there meanwhile
        f.truncate(size_bytes)
    r = host.run(['hdiutil', 'attach', '-imagekey', 'diskimage-class=CRawDiskImage', '-nomount', '-plist', str(image)],
                 timeout=300)
    for e in plistlib.loads(r.stdout).get('system-entities', []):
        dev = e.get('dev-entry', '')
        if dev.startswith('/dev/disk') and 's' not in dev[len('/dev/disk'):]:
            return dev[len('/dev/'):]
    raise StickError('hdiutil attached %s as no whole disk' % image)


OPENCORE_MAX_BYTES = 64 * MB        # OpenCore's ESP (about 20 MB): before the kit has built it
BASESYSTEM_MAX_BYTES = 1600 * MB    # Tahoe's x86_64 BaseSystem is 1.46 GB (25G342): the boot partition before it exists


def prepare(host, disk, opencore_tar, basesystem=None, on_step=None, expect=None):
    """Erase DISK first (create-installer then makes the large parts straight onto HAO). Returns the partitions."""
    size = Path(basesystem).stat().st_size if basesystem and Path(basesystem).is_file() else BASESYSTEM_MAX_BYTES
    mb = boot_mb(size, Path(opencore_tar).stat().st_size if opencore_tar else OPENCORE_MAX_BYTES)
    parts = partition(host, disk, mb, expect=expect)
    (on_step or (lambda text: None))('erased /dev/%s: %s (FAT32, %d MB) and %s (Mac OS Extended)' % (
        disk, BOOT_NAME, mb, KIT_NAME))
    (Path(parts[KIT_NAME][1]) / BUILD_DIR).mkdir(exist_ok=True)
    return parts


def write(host, disk, basesystem, opencore_tar, kit, hao_top, release=None, record=None, on_step=None, parts=None):
    """The whole stick on DISK (PARTS: already erased by prepare()). ON_STEP(text) is told each part once it is done
    and checked. Returns stick.json's content."""
    step = on_step or (lambda text: None)
    if parts is None:
        mb = boot_mb(Path(basesystem).stat().st_size, Path(opencore_tar).stat().st_size)
        parts = partition(host, disk, mb)
        step('erased /dev/%s: %s (FAT32, %d MB) and %s (Mac OS Extended)' % (disk, BOOT_NAME, mb, KIT_NAME))
    else:       # erased by prepare() before the downloads; the first step stays the erase (the console's parts)
        check_mounts(host, disk, parts)
        step('/dev/%s was erased first: %s and %s' % (disk, BOOT_NAME, KIT_NAME))
    rec = dict(record or {})
    rec.update(write_boot(parts['EFI'][1], parts[BOOT_NAME][1], basesystem, opencore_tar))
    step('EFI      OpenCore, as OCLP installs it (Bootstrap + EFI/OC)')
    step('%s  the Recovery (BaseSystem.dmg), sha256 checked' % BOOT_NAME)
    check_mounts(host, disk, parts)
    rec = write_kit(parts[KIT_NAME][1], kit, hao_top, release, rec)
    step('%s      hao, the kit (%.1f GB), install and report' % (KIT_NAME, rec['kit_bytes'] / 1e9))
    host.run(['diskutil', 'unmountDisk', '/dev/' + disk], check=False, timeout=300)
    return rec


KIT_ESP = Path('kit') / 'assembly-source' / 'esp'     # on HAO: the ESP the kit's install writes (EFI/OC + Bootstrap)


def esp_tar_of(tree, out):
    """TREE (an ESP folder: EFI/OC and the Bootstrap) as the tar opencore_files() reads."""
    if not (Path(tree) / 'EFI' / 'OC' / 'config.plist').is_file():
        raise StickError('%s has no EFI/OC/config.plist' % tree)
    with tarfile.open(out, 'w') as t:
        t.add(tree, arcname='.')
    return Path(out)


def rewrite_boot(host, disk, opencore_tar=None, basesystem=None, log=False, on_step=None, parts=None):
    """Only the boot partitions of a USB installer written by write() (HAO holds stick.json): OpenCore on EFI again
    (OPENCORE_TAR, or the ESP of the kit on HAO) and the Recovery on HAOBOOT (BASESYSTEM, or the one already there);
    HAO and the kit are not touched. OpenCore's logs on EFI (opencore-*.txt) are moved to HAO/reports/ first.
    Returns the updated stick.json."""
    step = on_step or (lambda text: None)
    parts = parts or mounted_parts(host, disk)       # found once: the same volumes until the end
    check_mounts(host, disk, parts)
    esp, boot, top = (Path(parts[n][1]) for n in ('EFI', BOOT_NAME, KIT_NAME))
    info = top / 'stick.json'
    if not info.is_file():
        raise StickError('/dev/%s is not a hao USB installer: %s has no stick.json' % (disk, KIT_NAME))
    if opencore_tar is None:
        if not (top / KIT_ESP / 'EFI' / 'OC' / 'config.plist').is_file():
            raise StickError("the kit on %s has no ESP (%s): name the target's ESP tar" % (KIT_NAME, KIT_ESP))
        import tempfile
        with tempfile.TemporaryDirectory() as work:
            return rewrite_boot(host, disk, esp_tar_of(top / KIT_ESP, Path(work) / 'kit-esp.tar'), basesystem, log,
                                on_step, parts)
    if basesystem is None:
        basesystem = boot / 'com.apple.recovery.boot' / 'BaseSystem.dmg'
        if not basesystem.is_file():
            raise StickError('%s has no Recovery (com.apple.recovery.boot/BaseSystem.dmg): name one' % BOOT_NAME)
    if info.is_symlink():
        raise StickError('%s/stick.json is a link: this drive was changed by something other than hao' % KIT_NAME)
    problems = check_manifest(top)          # before anything on the drive changes
    if problems:
        raise StickError('%s differs from its manifest (%s: %s): make the USB installer again' % (
            KIT_NAME, problems[0][0], problems[0][1]))
    rec = json.loads(read_plain(info))
    reports = real_folder(top / 'reports')
    logs = sorted(p.name for p in esp.glob('opencore-*.txt') if p.is_file() and not p.is_symlink())
    for name in logs:
        write_new(reports / name, read_plain(esp / name))
        (esp / name).unlink()
    if logs:
        step('kept %d OpenCore log%s in %s/reports' % (len(logs), '' if len(logs) == 1 else 's', KIT_NAME))
    for name in ('EFI', 'System'):
        shutil.rmtree(esp / name, ignore_errors=True)
    check_mounts(host, disk, parts)
    rec.update(write_boot(esp, boot, basesystem, opencore_tar, log=log))
    check_mounts(host, disk, parts)
    rec.update(boot_rewritten=datetime.datetime.now().isoformat(timespec='seconds'), opencore_logs_kept=logs)
    step('EFI      OpenCore again (picker after %d s, log %s)' % (PICKER_TIMEOUT, 'on' if log else 'off'))
    step('%s  the Recovery (BaseSystem.dmg), sha256 checked' % BOOT_NAME)
    write_new(info, (json.dumps(rec, indent=1, sort_keys=True) + '\n').encode())
    # stick.json changed (and nothing else on HAO): its entry alone follows
    m = json.loads(read_plain(top / MANIFEST))
    st = os.lstat(info)
    m['files']['stick.json'] = {'sha256': sha256(info), 'size': st.st_size, 'mode': '%04o' % (st.st_mode & 0o7777)}
    write_new(top / MANIFEST, (json.dumps(m, indent=1, sort_keys=True) + '\n').encode())
    host.run(['diskutil', 'unmountDisk', '/dev/' + disk], check=False, timeout=300)
    return rec


def main(argv):
    if len(argv) == 3 and argv[0] == 'opencore-config':
        c = opencore_config(plistlib.loads(Path(argv[1]).read_bytes()))
        Path(argv[2]).write_bytes(plistlib.dumps(c))
        return 0
    if argv[:1] == ['rewrite-boot']:
        return rewrite_boot_main(argv[1:])
    print(__doc__.rsplit('\n\n', 1)[-1], file=sys.stderr)
    return 2


def rewrite_boot_main(argv):
    import argparse
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from macos import SystemHost
    import disks as DISKS
    p = argparse.ArgumentParser(prog='stick.py rewrite-boot', description=rewrite_boot.__doc__)
    p.add_argument('disk', help='the USB installer, e.g. disk4 (diskutil list external)')
    p.add_argument('--opencore', help="an ESP tar (default: the ESP of the kit on the USB installer)")
    p.add_argument('--basesystem', help='a new BaseSystem.dmg (default: keep the one on HAOBOOT)')
    p.add_argument('--opencore-log', action='store_true', help="OpenCore's log to a file on EFI (slower start)")
    p.add_argument('--confirm', help='the disk name typed ahead (scripts)')
    a = p.parse_args(argv)
    host, disk = SystemHost(), DISKS.normalize(a.disk)
    if host.euid() != 0:
        print('stick.py rewrite-boot needs administrator rights: run it with sudo', file=sys.stderr)
        return 1
    why = DISKS.refusal(host, disk, 'external', 0)
    if why:
        print('stopped: /dev/%s may not be written: %s' % (disk, why), file=sys.stderr)
        return 1
    info, _ = DISKS.describe(host, disk)
    print('The boot partitions (EFI and HAOBOOT) of %s will be written again.' % DISKS.label(info))
    before = DISKS.identity(host, disk)
    if (a.confirm or input('Type %s to continue: ' % disk)).strip() != disk:
        print('stopped: not confirmed', file=sys.stderr)
        return 1
    why = DISKS.recheck(host, disk, before, 'external', 0)
    if why:
        print('stopped: /dev/%s was not written: %s' % (disk, why), file=sys.stderr)
        return 1
    try:
        rewrite_boot(host, disk, a.opencore, a.basesystem, log=a.opencore_log, on_step=print)
    except StickError as x:
        print('stopped: %s' % x, file=sys.stderr)
        return 1
    print('Done: /dev/%s has the new boot partitions; HAO and the kit are unchanged.' % disk)
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
