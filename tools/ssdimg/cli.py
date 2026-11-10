"""ssdimg: capture, version, edit and deploy bootable macOS SSD images; push them to a target Mac's internal disk from
a Recovery it loads over the network (push.py)."""
import argparse
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor

from . import apfs, bootcheck, disk, facts, generalize, gpt, push
from .store import Repo, StoreError, Progress, human, give_back_tree, WORKERS
from .store import _data_chunks as data_chunks

WRITE_BATCH = 32  # chunks per device write (8 MiB at 256 KiB chunks)
PREFETCH = WORKERS * 2  # device-write batches decompressed ahead of the writer (bounds RAM)


def die(msg):
    sys.stderr.write(f"ssdimg: {msg}\n")
    sys.exit(1)


def need_root(what):
    if os.geteuid() != 0:
        die(f"{what} needs raw disk access; run with sudo")


def give_back(*paths):
    """Files created under sudo belong to the invoking user."""
    uid, gid = os.environ.get("SUDO_UID"), os.environ.get("SUDO_GID")
    if os.geteuid() == 0 and uid:
        for p in paths:
            if os.path.exists(p):
                os.chown(p, int(uid), int(gid))


def need_detached(repo):
    dev = disk.attached_device(repo.work)
    if dev:
        die(f"working image is attached as {dev}; run `ssdimg detach` first")


def parse_size(s):
    s = s.strip().upper().rstrip("B")
    mult = {"K": 1e3, "M": 1e6, "G": 1e9, "T": 1e12}
    if s and s[-1] in mult:
        return int(float(s[:-1]) * mult[s[-1]])
    return int(s)


def image_gpt(path, sector):
    r = disk.Reader(path, align=sector)
    try:
        return gpt.GPT(r, sector)
    finally:
        r.close()


def apply_writes(path, writes):
    fd = os.open(path, os.O_RDWR)
    try:
        for off, data in writes:
            disk.write_all(fd, data, off)
        os.fsync(fd)
    finally:
        os.close(fd)


def work_is_clean(repo):
    """True if there is no working image, or it matches HEAD chunk for chunk."""
    if not os.path.exists(repo.work):
        return True
    head = repo.manifest("HEAD") if _has_head(repo) else {}
    return _scan(repo)[0] == head


# ---------------------------------------------------------------- init

def cmd_init(a):
    if os.path.exists(os.path.join(a.dir, "image.json")):
        die(f"{a.dir} is already an ssdimg repository")
    r = Repo.init(a.dir, chunk_size=parse_size(a.chunk_size))
    print(f"initialized empty image repository in {r.root}")


# ---------------------------------------------------------------- capture

def keep_ranges(read, size, sector, full=False):
    """Byte ranges of `read` that hold anything meaningful: MBR + both GPTs,
    every non-APFS partition whole, and only the allocated blocks (plus fixed
    metadata) of each APFS container. Returns (gpt, [(offset, length)])."""
    g = gpt.GPT(read, sector)
    keep = [(0, g.first_usable * sector),                       # MBR + primary GPT
            ((g.alt_lba - g.entries_sectors) * sector, size)]   # backup GPT
    for p in g.partitions:
        off, psize = p.offset(sector), p.size(sector)
        if p.type == gpt.TYPE_APFS and not full:
            try:
                c = apfs.Container(read, off, psize)
                ranges, st = c.allocation()
                print(f"  APFS part {p.index}: {st['used_blocks_bitmap']} of {st['block_count']} blocks "
                      f"allocated (free {st['free_blocks']}); {human(st['copy_blocks'] * st['block_size'])} in use")
                if st["block_count"] - st["free_blocks"] != st["used_blocks_bitmap"]:
                    print("  note: bitmap used count differs from device free count "
                          f"by {st['block_count'] - st['free_blocks'] - st['used_blocks_bitmap']} "
                          "blocks (free queue); extra ranges are copied")
                keep += [(off + s * c.bs, n * c.bs) for s, n in ranges]
                continue
            except (apfs.ApfsError, struct.error, disk.DiskError) as e:
                print(f"  APFS part {p.index}: cannot read allocation map ({e}); treating whole partition as in use")
        keep.append((off, psize))
    return g, keep


def chunk_masks(keep, size, cs):
    """chunk index -> [(lo, hi)] byte ranges inside that chunk that are in use."""
    masks = {}
    for s, n in sorted(keep):
        e = min(s + n, size)
        for i in range(s // cs, (e - 1) // cs + 1):
            lo, hi = max(s, i * cs) - i * cs, min(e, (i + 1) * cs) - i * cs
            masks.setdefault(i, []).append((lo, hi))
    return masks


def capture_core(src, out_path, cs, full=False, label="capture", verbose=True, reader=None):
    """Copy the GPT, every non-APFS partition and the allocated blocks of every
    APFS container from `src` (device or raw file) into the sparse file
    `out_path`. Free space becomes holes. Returns (size, sector, gpt).
    `reader` = (read, size, sector) reads a remote source instead (ssdimg pull)."""
    if reader:
        read, size, sector = reader
    else:
        size, sector = disk.device_size(src)
    if sector != 512:
        raise disk.DiskError(f"{src} has {sector}-byte sectors; ssdimg images are tied to 512-byte sectors")
    if not reader:
        read = disk.Reader(src, align=max(sector, 4096), size=size)
    try:
        if verbose:
            print(f"source: {src}  {human(size)}  sector {sector}")
        g, keep = keep_ranges(read, size, sector, full)
        if verbose:
            print(g.describe(), end="")
        masks = chunk_masks(keep, size, cs)
        idx = sorted(masks)
        total = len(idx)
        print(f"reading {total} chunks ({human(total * cs)}) ...")

        batches, k = [], 0
        while k < total:
            j = k
            while j + 1 < total and idx[j + 1] == idx[j] + 1 and j + 1 - k < WRITE_BATCH:
                j += 1
            batches.append((k, j))
            k = j + 1
        if hasattr(read, "plan"):      # remote reader: request ahead, in exactly this order
            read.plan([(idx[k] * cs, min((idx[j] + 1) * cs, size) - idx[k] * cs) for k, j in batches])

        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        out = os.open(out_path, os.O_RDWR | os.O_CREAT | os.O_TRUNC, 0o644)
        os.ftruncate(out, size)
        prog = Progress(label)
        t0 = time.time()
        done = 0
        zero = bytes(cs)
        try:
            for k, j in batches:
                start = idx[k] * cs
                length = min((idx[j] + 1) * cs, size) - start
                buf = read(start, length)
                for i in idx[k:j + 1]:
                    part = buf[i * cs - start:(i + 1) * cs - start]
                    rngs = masks[i]
                    if not (len(rngs) == 1 and rngs[0] == (0, len(part))):
                        clean = bytearray(len(part))
                        for lo, hi in rngs:
                            clean[lo:hi] = part[lo:hi]
                        part = bytes(clean)
                    if part != zero[:len(part)]:
                        disk.write_all(out, part, i * cs)
                done += j - k + 1
                prog(done, total)
            os.fsync(out)
        finally:
            os.close(out)
        dt = time.time() - t0
        print(f"{label}: {human(total * cs)} in {dt:.0f}s ({human(total * cs / max(dt, 1e-3))}/s)")
    finally:
        read.close()
    return size, sector, g


def cmd_capture(a):
    repo = Repo.find(a.repo)
    src = a.source
    if _is_device(src):
        need_root("capture")
        src = disk.normalize(src)
        whole = disk.whole_disk(src)
        if whole != src:
            die(f"{src} is a partition; pass the whole disk ({whole})")
        if whole in disk.boot_disks():
            die(f"{whole} is the running system's disk")
        mounts = disk.mounted_under(whole)
        if mounts:
            die("volumes on the source are mounted; an APFS container must be cleanly "
                f"unmounted to be imaged consistently.\n  run: diskutil unmountDisk {whole}\n  mounted: "
                + ", ".join(mounts))
        src = disk.raw(whole)
    elif disk.attached_device(src):
        die(f"{src} is attached; detach it first")
    need_detached(repo)
    scrub = os.path.realpath(src) == os.path.realpath(repo.work)
    if not scrub and not a.force and not work_is_clean(repo):
        die("working image has uncommitted changes; commit them or pass --force to overwrite it")

    tmp = repo.work + ".capture"
    size, sector, g = capture_core(src, tmp, repo.meta()["chunk_size"], full=a.full,
                                   label="scrub" if scrub else "capture")
    os.replace(tmp, repo.work)
    give_back(repo.work)
    if scrub:
        return
    m = repo.meta()
    m.update(size=size, sector_size=sector, source=a.source)
    repo.save_meta(m)
    with open(repo.layout_path, "w") as f:
        f.write(g.describe())
    give_back(repo.meta_path, repo.layout_path)
    print("next: `ssdimg fsck`, `ssdimg commit -m ...`, then `sudo ssdimg generalize` and `ssdimg shrink`")


def cmd_scrub(a):
    """Zero all free space in the working image (re-copy allocated blocks only)."""
    repo = Repo.find(a.repo)
    print("scrubbing free space in the working image ...")
    cmd_capture(argparse.Namespace(repo=a.repo, source=repo.work, full=False, force=True))


# ---------------------------------------------------------------- shrink

def cmd_shrink(a):
    repo = Repo.find(a.repo)
    need_detached(repo)
    m = repo.meta()
    whole, ents = disk.attach(repo.work, mount=False)
    try:
        g = image_gpt(repo.work, m["sector_size"])
        parts = g.apfs_partitions()
        if len(parts) != 1:
            die("expected exactly one APFS partition")
        pdev = f"{whole}s{parts[0].index}"
        if a.size:
            target = parse_size(a.size)
        else:
            _ref, _vols = disk.apfs_volumes(pdev)
            c = next(c for c in disk.plist("diskutil", "apfs", "list", "-plist")["Containers"]
                     if c["ContainerReference"] == _ref)
            used = c["CapacityCeiling"] - c["CapacityFree"]
            target = -(-(used + parse_size(a.headroom)) // 10**9) * 10**9
        print(f"resizing APFS container {pdev} to {human(target)} ...")
        p = subprocess.run(["diskutil", "apfs", "resizeContainer", pdev, f"{target // 10**6}M"],
                           capture_output=True, text=True)
        out = p.stdout
        if p.returncode or "Finished APFS operation" not in out:
            # diskutil puts the reason (e.g. "not enough free space") on stderr
            die("resize failed; image left unchanged in size:\n" + (out + p.stderr).strip())
        for line in out.splitlines():
            if any(k in line for k in ("minimum size", "Shrinking APFS Physical", "exit code")):
                print("  " + line.strip())
    finally:
        disk.detach(whole)
    g = image_gpt(repo.work, m["sector_size"])
    total = g.min_disk_sectors()
    apply_writes(repo.work, g.relocated(total))
    os.truncate(repo.work, total * m["sector_size"])
    m["size"] = total * m["sector_size"]
    repo.save_meta(m)
    with open(repo.layout_path, "w") as f:
        f.write(image_gpt(repo.work, m["sector_size"]).describe())
    give_back(repo.work, repo.meta_path, repo.layout_path)
    print(f"image is now {human(m['size'])}; it fits any SSD at least that large")
    cmd_scrub(a)


# ---------------------------------------------------------------- attach / detach / fsck

def cmd_attach(a):
    repo = Repo.find(a.repo)
    if disk.attached_device(repo.work):
        die(f"already attached as {disk.attached_device(repo.work)}")
    # Attach without auto-mounting and mount Data and System explicitly, so this works the same
    # when /etc/fstab marks the image's volume UUIDs noauto (auto-mount would find nothing to mount).
    whole, ents = disk.attach(repo.work, mount=False, readonly=a.readonly, owners=a.owners)
    print(f"attached {repo.work} as {whole}")
    efi = disk.part_dev(ents, disk.EFI_GUID)
    if efi and not a.no_efi:
        r = subprocess.run(["diskutil", "mount"] + (["readOnly"] if a.readonly else []) + [efi],
                           capture_output=True, text=True)
        print("  EFI: " + (r.stdout.strip() or r.stderr.strip()))
    try:
        ref, vols = disk.apfs_volumes(disk.part_dev(ents, disk.APFS_GUID))
        for v in vols:
            if not {"Data", "System"} & set(v.get("Roles", [])) or disk.info(v["DeviceIdentifier"]).get("MountPoint"):
                continue
            opts = ["readOnly"] if a.readonly else (["-mountOptions", "owners"] if a.owners else [])
            r = subprocess.run(["diskutil", "mount", *opts, v["DeviceIdentifier"]], capture_output=True, text=True)
            if r.returncode:
                print(f"  warning: could not mount {v['DeviceIdentifier']}: {(r.stderr or r.stdout).strip()}")
        for v in vols:
            mp = disk.info(v["DeviceIdentifier"]).get("MountPoint") or "(not mounted)"
            print(f"  /dev/{v['DeviceIdentifier']:<11} {'/'.join(v.get('Roles', [])) or '-':<10} {v['Name']:<20} {mp}")
    except disk.DiskError:
        pass
    print("edit away, then `ssdimg detach` and `ssdimg commit -m ...`")


def cmd_detach(a):
    repo = Repo.find(a.repo)
    dev = disk.attached_device(repo.work)
    if not dev:
        die("working image is not attached")
    disk.detach(dev, force=a.force)
    print(f"detached {dev}")


def run_fsck(repo, quiet=False):
    """fsck_msdos + fsck_apfs (read-only) on the working image. Returns True if clean."""
    need_detached(repo)
    whole, ents = disk.attach(repo.work, mount=False, readonly=True)
    ok = True
    try:
        checks = [(disk.part_dev(ents, disk.EFI_GUID), "fsck_msdos"),
                  (disk.part_dev(ents, disk.APFS_GUID), "fsck_apfs")]
        # HFS+ partitions (e.g. a createinstallmedia installer volume), checked read-only too
        checks += [(e["dev-entry"], "fsck_hfs") for e in ents
                   if e.get("unmapped-content-hint", e.get("content-hint")) == disk.HFS_GUID
                   or e.get("content-hint") == "Apple_HFS"]
        if not any(d for d, _ in checks):
            die("no EFI, APFS or HFS+ partitions found in the image")
        for dev, tool in checks:
            if not dev:
                continue
            cmd = [tool, "-fn" if tool == "fsck_hfs" else "-n", dev]  # fsck_hfs skips journaled volumes without -f
            print(f"$ {' '.join(cmd)}")
            p = subprocess.run(cmd, capture_output=True, text=True)
            out = (p.stdout + p.stderr).strip()
            if quiet and p.returncode == 0:
                lines = out.splitlines()
                keep = [l for l in lines if "appears to be OK" in l or "clean" in l.lower()]
                out = "\n".join(keep or lines[-1:])
            print(out)
            ok &= p.returncode == 0
    finally:
        disk.detach(whole)
    print("fsck: OK" if ok else "fsck: PROBLEMS FOUND")
    return ok


def cmd_fsck(a):
    sys.exit(0 if run_fsck(Repo.find(a.repo)) else 1)


# ---------------------------------------------------------------- versioning

def _scan(repo, store=False):
    return repo.scan(store=store, progress=Progress("hashing" if not store else "storing"))


def _diff(old, new, cs):
    changed = sum(1 for i in new if old.get(i) != new[i])
    removed = sum(1 for i in old if i not in new)
    return changed, removed


def cmd_status(a):
    repo = Repo.find(a.repo)
    if not os.path.exists(repo.work):
        die("no working image (capture or checkout first)")
    cs = repo.meta()["chunk_size"]
    head = repo.manifest("HEAD") if _has_head(repo) else {}
    cur, _, _ = _scan(repo)
    changed, removed = _diff(head, cur, cs)
    print(f"working image: {human(os.path.getsize(repo.work))} logical, {len(cur)} non-zero chunks "
          f"({human(len(cur) * cs)})")
    if not changed and not removed:
        print("clean (matches HEAD)")
    else:
        print(f"modified vs HEAD: {changed} chunks changed/added, {removed} zeroed "
              f"(~{human((changed + removed) * cs)})")
    if disk.attached_device(repo.work):
        print(f"note: image is attached as {disk.attached_device(repo.work)}; detach before committing")


def _has_head(repo):
    return repo.git("rev-parse", "--verify", "-q", "HEAD", capture=True, check=False).strip() != ""


def cmd_commit(a):
    repo = Repo.find(a.repo)
    need_detached(repo)
    if not os.path.exists(repo.work):
        die("no working image")
    m = repo.meta()
    if os.path.getsize(repo.work) != m["size"]:
        m["size"] = os.path.getsize(repo.work)
        repo.save_meta(m)
    t0 = time.time()
    man, new, nbytes = _scan(repo, store=True)
    old = repo.manifest("HEAD") if _has_head(repo) else {}
    changed, removed = _diff(old, man, m["chunk_size"])
    try:
        layout = image_gpt(repo.work, m["sector_size"]).describe()
    except gpt.GPTError as e:
        layout = f"# GPT unreadable: {e}\n"
    repo.save_manifest(man, layout)
    repo.git("add", "image.json", "image.manifest", "layout.txt", ".gitignore")
    msg = a.message + f"\n\nssdimg: {changed} chunks changed, {removed} zeroed; {new} new objects ({human(nbytes)} stored)"
    r = repo.git("commit", "-q", "-m", msg, check=False)
    if r.returncode:
        die("git commit failed (nothing changed?)")
    rev = repo.git("rev-parse", "--short", "HEAD", capture=True).strip()
    print(f"[{rev}] {a.message}")
    print(f"  {changed} chunks changed, {removed} zeroed, {new} new objects, {human(nbytes)} added to store "
          f"in {time.time() - t0:.0f}s")


def cmd_log(a):
    repo = Repo.find(a.repo)
    repo.git("log", "--format=%C(yellow)%h%Creset %ad %C(green)%d%Creset %s%n         %b",
             "--date=format:%Y-%m-%d %H:%M", *(["-n", str(a.n)] if a.n else []))


def _materialize_to(repo, rev, force, label):
    need_detached(repo)
    target = repo.manifest(rev)
    tmeta = repo.meta(rev)
    current = None
    if os.path.exists(repo.work):
        current, _, _ = _scan(repo)
        head = repo.manifest("HEAD") if _has_head(repo) else {}
        if current != head and not force:
            die("working image has uncommitted changes; commit them or pass --force to discard")
    missing = [h for h in set(target.values()) if not repo.has(h)]
    if missing:
        die(f"{len(missing)} objects for {rev} are missing from the store")
    w, z = repo.materialize(target, tmeta["size"], current=current, progress=Progress(label))
    return w, z


def cmd_checkout(a):
    """Restore the working image to REV without moving the branch (non-destructive undo)."""
    repo = Repo.find(a.repo)
    w, z = _materialize_to(repo, a.rev, a.force, "checkout")
    repo.git("checkout", a.rev, "--", "image.json", "image.manifest", "layout.txt")
    print(f"working image now matches {a.rev} ({w} chunks written, {z} zeroed)")
    print("commit it to record the undo on top of history, e.g. "
          f"`ssdimg commit -m \"Revert to {a.rev}\"`")


def cmd_reset(a):
    """Move the branch back to REV and restore the image (drops later commits)."""
    repo = Repo.find(a.repo)
    w, z = _materialize_to(repo, a.rev, a.force, "reset")
    repo.git("reset", "-q", "--hard", a.rev)
    print(f"HEAD is now {a.rev} ({w} chunks written, {z} zeroed); `ssdimg gc` frees dropped data")


def cmd_sync(a):
    """Rebuild the working image from the manifest in the git worktree
    (use after `git checkout <branch>`)."""
    repo = Repo.find(a.repo)
    need_detached(repo)
    current = _scan(repo)[0] if os.path.exists(repo.work) else None
    w, z = repo.materialize(repo.manifest(), repo.meta()["size"], current=current, progress=Progress("sync"))
    print(f"working image synced ({w} chunks written, {z} zeroed)")


def cmd_export(a):
    repo = Repo.find(a.repo)
    rev = a.rev
    man = repo.manifest(rev) if rev else repo.manifest()
    size = (repo.meta(rev) if rev else repo.meta())["size"]
    if os.path.exists(a.path) and not a.force:
        die(f"{a.path} exists")
    if getattr(a, "part", None):
        # One partition's bytes only (e.g. the EFI system partition with OpenCore), as a raw
        # file that `hdiutil attach -imagekey diskimage-class=CRawDiskImage` can mount.
        sector = (repo.meta(rev) if rev else repo.meta())["sector_size"]
        read = repo.reader(man, size)
        g = gpt.GPT(read, sector)
        p = next((p for p in g.partitions if p.index == a.part), None)
        if not p:
            die(f"no partition {a.part} in {rev or 'the worktree manifest'} "
                f"(have {', '.join(f'{q.index}={q.type_name}' for q in g.partitions)})")
        off, n = p.offset(sector), p.size(sector)
        step = 8 << 20
        fd = os.open(a.path, os.O_RDWR | os.O_CREAT | os.O_TRUNC, 0o644)
        try:
            os.ftruncate(fd, n)
            for o in range(0, n, step):
                buf = read(off + o, min(step, n - o))
                if buf.count(0) != len(buf):
                    disk.write_all(fd, buf, o)
            os.fsync(fd)
        finally:
            os.close(fd)
        print(f"wrote partition {p.index} ({p.type_name}, {p.name!r}) of {rev or 'worktree'}: "
              f"{a.path} ({human(n)}, sparse)")
        return
    repo.materialize(man, size, path=os.path.abspath(a.path), progress=Progress("export"))
    print(f"wrote raw image {a.path} ({human(size)} logical, sparse)")


def cmd_bundle(a):
    """A commit as an image bundle for the Recovery's Reinstall macOS (streamfile.py)."""
    from . import streamfile
    keys = {"model": a.model, "macos_version": a.macos_version, "macos_build": a.macos_build}
    streamfile.export(Repo.find(a.repo), a.rev, a.path, a.name, streamfile.parse_size(a.segment_size), keys)


def cmd_bundle_check(a):
    from . import streamfile
    streamfile.check(a.path)


def cmd_gc(a):
    repo = Repo.find(a.repo)
    refs = repo.referenced()
    if os.path.exists(repo.work) and not disk.attached_device(repo.work):
        refs |= set(_scan(repo)[0].values())
    freed = n = 0
    for h, p in repo.objects_on_disk():
        if h not in refs:
            freed += os.path.getsize(p)
            n += 1
            if not a.dry_run:
                os.unlink(p)
    print(f"{'would remove' if a.dry_run else 'removed'} {n} unreferenced objects ({human(freed)})")
    if not a.dry_run:
        repo.git("gc", "-q", "--auto", check=False)


def cmd_info(a):
    repo = Repo.find(a.repo)
    m = repo.meta()
    print(f"repository:  {repo.root}")
    print(f"image size:  {human(m['size'])} ({m['size']} bytes), sector {m['sector_size']}, chunk {human(m['chunk_size'])}")
    if m.get("facts"):
        print(f"contents:    {facts.describe(m['facts'])}")
    if _has_head(repo):
        print("HEAD:        " + repo.git("log", "-1", "--format=%h %s", capture=True).strip())
    man = repo.manifest()
    print(f"data:        {len(man)} non-zero chunks ({human(len(man) * m['chunk_size'])} uncompressed)")
    n = sz = 0
    for _h, p in repo.objects_on_disk():
        n += 1
        sz += os.path.getsize(p)
    print(f"store:       {n} objects, {human(sz)} on disk")
    if os.path.exists(repo.layout_path):
        print("layout:\n  " + open(repo.layout_path).read().strip().replace("\n", "\n  "))


# ---------------------------------------------------------------- generalize

def _mount_flags(mp):
    """The mount(8) flags of the volume mounted at `mp`, e.g. ['apfs', 'noowners']."""
    for line in subprocess.run(["mount"], capture_output=True, text=True).stdout.splitlines():
        if f" on {mp} (" in line:
            return line.rsplit("(", 1)[1].rstrip(")").replace(" ", "").split(",")
    return []


GENERALIZE_ROLES = ("Data", "Preboot", "VM")


def _mount_volumes(repo, readonly, roles=GENERALIZE_ROLES):
    """Attach the working image without mounting anything, then mount only the
    volumes with the given APFS roles: read-only for a dry run, otherwise
    read-write with file ownership honoured. Returns (whole_dev, {role: (dev, mount_point)});
    a Data volume is required, the other roles are optional."""
    whole, ents = disk.attach(repo.work, mount=False, readonly=readonly)
    try:
        store = disk.part_dev(ents, disk.APFS_GUID)
        if not store:
            die("no APFS partition in the image")
        _ref, vols = disk.apfs_volumes(store)
        mounts = {}
        for role in roles:
            v = next((v for v in vols if role in v.get("Roles", [])), None)
            if not v:
                if role == "Data":
                    die("no Data-role volume in the image")
                continue
            dev = "/dev/" + v["DeviceIdentifier"]
            mp = disk.info(dev).get("MountPoint")
            if not mp:
                # A plain `diskutil mount` ignores ownership on external volumes, which
                # would store anything generalize creates as owner 99 ("unknown").
                opts = ["readOnly"] if readonly else ["-mountOptions", "owners"]
                disk.run("diskutil", "mount", *opts, dev)
                mp = disk.info(dev).get("MountPoint")
            if not mp or mp == "/" or mp.startswith("/System/Volumes/"):
                die(f"refusing to operate on {mp!r}")
            flags = _mount_flags(mp)
            if readonly and "read-only" not in flags:
                die(f"{mp} did not mount read-only ({', '.join(flags)})")
            if not readonly and "noowners" in flags:
                die(f"{mp} mounted with ownership ignored ({', '.join(flags)}); refusing to edit")
            mounts[role] = (dev, mp)
        return whole, mounts
    except BaseException:
        disk.detach(whole)
        raise


def cmd_generalize(a):
    need_root("generalize")
    repo = Repo.find(a.repo)
    need_detached(repo)
    # A dry run mounts read-only, so looking changes nothing in the image.
    whole, mounts = _mount_volumes(repo, readonly=not a.yes)
    try:
        for role, (dev, mp) in mounts.items():
            print(f"{role} volume {dev} mounted at {mp}{'' if a.yes else ' (read-only)'}")
        print()
        ddev, mp = mounts["Data"]
        plan = generalize.build(mp, data_dev=ddev, keep_users=a.keep_user or (), clean_logs=not a.keep_logs,
                                preboot=mounts.get("Preboot", (None, None))[1], vm=mounts.get("VM", (None, None))[1])
        if not plan.steps:
            print("nothing to do: already generalized")
        for desc, _ in plan.steps:
            print(f"  - {desc}")
        for n in plan.notes:
            print(f"\nnote: {n}")
        if not plan.steps:
            return
        if not a.yes:
            print("\ndry run only; re-run with --yes to apply")
            return
        print()
        for desc, fn in plan.steps:
            try:
                warning = fn()
            except Exception as e:  # noqa: BLE001 - report which step failed, then stop
                die(f"step failed: {desc}\n  {e}\n"
                    "The image is partially generalized. Restore it with `ssdimg checkout HEAD`, "
                    "or fix the cause and re-run generalize --yes (completed steps are skipped).")
            print(f"  done: {desc}" + (f"\n    warning: {warning}" if warning else ""))
        # When the volume unmounts, fseventsd writes a log of this session's
        # events (every path just deleted) into .fseventsd. The no_log marker
        # makes it skip that.
        generalize.fsevents_marker(mp, True)
        subprocess.run(["sync"])
    finally:
        disk.detach(whole)
    # Short second session to drop the marker again, so the deployed Mac keeps
    # logging file-system events (Spotlight and Time Machine rely on them). The
    # log fseventsd writes for this session names only .fseventsd/no_log.
    whole, mounts = _mount_volumes(repo, readonly=False, roles=("Data",))
    try:
        generalize.fsevents_marker(mounts["Data"][1], False)
        print("  done: remove .fseventsd/no_log marker")
        subprocess.run(["sync"])
    finally:
        disk.detach(whole)
    cmd_scrub(a)
    print("\ngeneralized and scrubbed. Review with `ssdimg status`, then commit.")


# ---------------------------------------------------------------- deploy

def _is_device(s):
    return s.startswith("/dev/") or s.startswith("disk") or s.startswith("rdisk")


def _flush(fd):
    try:
        os.fsync(fd)
    except OSError as e:  # raw device nodes are unbuffered; some refuse fsync
        print(f"  (fsync not supported on this device: {e.strerror})")


def _batches(indices):
    """Group chunk indices into runs of consecutive chunks, at most WRITE_BATCH long."""
    out, idx, k = [], sorted(indices), 0
    while k < len(idx):
        j = k
        while j + 1 < len(idx) and idx[j + 1] == idx[j] + 1 and j + 1 - k < WRITE_BATCH:
            j += 1
        out.append(range(idx[k], idx[j] + 1))
        k = j + 1
    return out


def fit_to_target(repo, man, meta, tsize, label):
    """Scratch copy of the image with its APFS container shrunk so the whole
    image fits a disk of `tsize` bytes. The repository is not modified.
    Returns (scratch_path, new_image_size)."""
    sector, cs = meta["sector_size"], meta["chunk_size"]
    tmp = os.path.join(repo.root, "work", f"fit-{label}-{os.getpid()}.img")
    scrubbed = tmp + ".scrub"
    try:
        repo.materialize(man, meta["size"], path=tmp, progress=Progress("scratch copy"))
        whole, _ents = disk.attach(tmp, mount=False)
        try:
            g = image_gpt(tmp, sector)
            parts = g.apfs_partitions()
            if len(parts) != 1:
                die("expected exactly one APFS partition")
            p = parts[0]
            total = tsize // sector
            # Room for the backup GPT plus 1 MiB of alignment slack, rounded to MiB.
            cont = ((total - g.entries_sectors - 2 - p.first_lba) // 2048 - 1) * 2048
            if cont <= 0:
                die(f"target ({human(tsize)}) is too small for even an empty container")
            pdev = f"{whole}s{p.index}"
            print(f"shrinking the APFS container of a scratch copy to {human(cont * sector)} to fit the target ...")
            r = subprocess.run(["diskutil", "apfs", "resizeContainer", pdev, str(cont * sector)],
                               capture_output=True, text=True)
            text = (r.stdout + r.stderr).strip()
            for line in text.splitlines():
                if any(k in line for k in ("minimum size", "Shrinking APFS Physical", "rror")):
                    print("  " + line.strip())
            if r.returncode or "Finished APFS operation" not in text:
                die(f"the container cannot be shrunk to fit {human(tsize)}; nothing was written")
        finally:
            disk.detach(whole)
        g = image_gpt(tmp, sector)
        new_total = g.min_disk_sectors()
        if new_total > total:
            die(f"image still needs {human(new_total * sector)} after shrinking; target is {human(tsize)}")
        apply_writes(tmp, g.relocated(new_total))
        os.truncate(tmp, new_total * sector)
        # Re-copy only allocated blocks: data diskutil moved must not linger in free space.
        capture_core(tmp, scrubbed, cs, label="scrub", verbose=False)
        os.replace(scrubbed, tmp)
        return tmp, new_total * sector
    except BaseException:
        for pth in (tmp, scrubbed):
            if os.path.exists(pth):
                os.unlink(pth)
        raise


def cmd_deploy(a):
    target = a.target
    is_file = not _is_device(target)  # testing aid: deploy into a pre-sized raw file
    if not is_file:
        need_root("deploy")
        target = disk.normalize(target)
    repo = Repo.find(a.repo)
    rev = a.rev
    man = repo.manifest(rev) if rev else repo.manifest()
    meta = repo.meta(rev) if rev else repo.meta()
    if not rev:
        if not os.path.exists(repo.work):
            die("nothing to deploy")
        need_detached(repo)
        if _has_head(repo) and man != repo.manifest("HEAD"):
            die("worktree manifest differs from HEAD; commit first or pass --rev")
        cur = _scan(repo)[0]
        if cur != man:
            die("working image has uncommitted changes; commit first (deploys come from commits)")
    if not man:
        die("empty manifest")
    missing = [h for h in set(man.values()) if not repo.has(h)]
    if missing:
        die(f"{len(missing)} objects missing from store")
    size, cs, sector = meta["size"], meta["chunk_size"], meta["sector_size"]
    wipe = getattr(a, "wipe", False)

    if is_file:
        tsize, tsector = os.path.getsize(target), 512
        dev_raw = target
    else:
        i = disk.info(target)
        if not i.get("WholeDisk"):
            die(f"{target} is not a whole disk")
        if target in disk.boot_disks():
            die(f"{target} is the running system's disk")
        if i.get("VirtualOrPhysical") == "Virtual" and not a.allow_virtual:
            die(f"{target} is a virtual disk (disk image?); pass --allow-virtual to permit")
        if i.get("Internal") and not a.allow_internal:
            die(f"{target} is an internal disk; pass --allow-internal if you really mean it")
        if getattr(a, "strict", False):
            disk.external_whole(target)  # `write`: external physical USB/Thunderbolt only, no overrides
        tsize, tsector = i["TotalSize"], i.get("DeviceBlockSize", 512)
        print(f"target: {disk.summary(i)}")
        mounts = disk.mounted_under(target)
        if mounts:
            print("  currently mounted (will be unmounted): " + ", ".join(mounts))
        dev_raw = disk.raw(target)
    if tsector != sector:
        die(f"target sector size {tsector} != image sector size {sector}")
    label = rev or (repo.git("rev-parse", "--short", "HEAD", capture=True).strip() if _has_head(repo) else "worktree")
    print(f"image: {label}  {human(size)} logical  ({len(man)} data chunks, {human(len(man) * cs)})")

    scratch = sfd = None
    try:
        if tsize < size:
            print(f"target ({human(tsize)}) is smaller than the image ({human(size)}); fitting a scratch copy to it")
            scratch, size = fit_to_target(repo, man, meta, tsize, label)
            print(f"fitted image: {human(size)}")
        if scratch:
            sfd = os.open(scratch, os.O_RDONLY)
            nonzero = set(data_chunks(scratch, size, cs))
            src_read = disk.Reader(scratch, align=4096, size=size)

            def get_chunk(i):
                d = os.pread(sfd, cs, i * cs)
                return d if len(d) == cs else d.ljust(cs, b"\0")
        else:
            nonzero = set(man)
            src_read = repo.reader(man, size)

            def get_chunk(i):
                return repo.get(man[i])
        nchunks = (size + cs - 1) // cs
        zero = bytes(cs)
        if wipe:
            present = set(range(nchunks))
        else:
            # Skip only space the partition table / APFS allocator says is unused.
            # A chunk that is all zeros but *in use* (FAT tables, zero-filled file
            # data, bitmaps) must still be written, or old data on the target shows
            # through. So the skip set comes from the allocation map, not from the
            # manifest.
            _g, keep = keep_ranges(src_read, size, sector)
            present = nonzero | set(chunk_masks(keep, size, cs))
        if scratch:
            src_read.close()
        batches = _batches(present)
        nwrite = sum(len(b) for b in batches)

        def load(batch):
            return b"".join(get_chunk(i) if i in nonzero else zero for i in batch)

        print(f"\nALL DATA ON {target} WILL BE DESTROYED.")
        if not a.yes:
            ans = input(f"type the target name ({os.path.basename(target)}) to continue: ").strip()
            if ans != os.path.basename(target):
                die("aborted")
        if not is_file:
            disk.run("diskutil", "unmountDisk", "force", target)
        print(f"writing {nwrite} chunks ({human(nwrite * cs)})"
              + (", zeroing free space too (--wipe)" if wipe else
                 f", skipping {human((nchunks - nwrite) * cs)} of unallocated space") + " ...")

        fd = os.open(dev_raw, os.O_RDWR)
        t0 = time.time()
        prog = Progress("write")
        try:
            # Decompress at most PREFETCH batches ahead of the device writer so RAM
            # stays bounded (~PREFETCH * WRITE_BATCH * chunk) however slow the SSD is.
            with ThreadPoolExecutor(WORKERS) as ex:
                pending = deque()
                todo = iter(batches)
                done = 0
                for b in todo:
                    pending.append((b, ex.submit(load, b)))
                    if len(pending) >= PREFETCH:
                        break
                while pending:
                    b, fut = pending.popleft()
                    off = b.start * cs
                    disk.write_all(fd, fut.result()[:size - off], off)
                    done += len(b)
                    prog(done, nwrite)
                    nb = next(todo, None)
                    if nb is not None:
                        pending.append((nb, ex.submit(load, nb)))
            _flush(fd)
            dt = time.time() - t0
            print(f"wrote {human(nwrite * cs)} in {dt:.0f}s ({human(nwrite * cs / max(dt, 1e-3))}/s)")

            if a.verify:
                vprog = Progress("verify")
                bad = done = 0
                for b in batches:
                    off = b.start * cs
                    want = min(len(b) * cs, size - off)
                    got = os.pread(fd, want, off)
                    if len(got) != want:
                        die(f"verify FAILED: short read of {len(got)}/{want} bytes at {off}")
                    for i in b:
                        chunk = got[(i - b.start) * cs:(i - b.start + 1) * cs]
                        if chunk != load([i])[:len(chunk)]:
                            bad += 1
                    done += len(b)
                    vprog(done, nwrite)
                if bad:
                    die(f"verify FAILED: {bad} chunks differ")
                print("verify: OK")

            # Move the backup GPT to the end of the target disk.
            rd = disk.Reader(dev_raw, align=sector, size=tsize)
            g = gpt.GPT(rd, sector)
            rd.close()
            for off, data in g.relocated(tsize // sector):
                disk.write_all(fd, data, off)
            _flush(fd)
        finally:
            os.close(fd)
    finally:
        if sfd is not None:
            os.close(sfd)
        if scratch and os.path.exists(scratch):
            os.unlink(scratch)
    print(f"partition table adjusted for {human(tsize)}")

    if is_file or a.no_grow:
        print("done (container not grown)")
        return
    apfs_part = g.apfs_partitions()[0]
    pdev = f"{target}s{apfs_part.index}"
    if not disk.wait_for(lambda: disk.info(pdev).get("Content") == "Apple_APFS", timeout=60):
        print(f"warning: {pdev} did not appear; grow manually: sudo diskutil apfs resizeContainer {pdev} 0")
        return
    print(f"growing APFS container on {pdev} to fill the disk ...")
    r = subprocess.run(["diskutil", "apfs", "resizeContainer", pdev, "0"], capture_output=True, text=True)
    print((r.stdout + r.stderr).strip().splitlines()[-1] if (r.stdout + r.stderr).strip() else "")
    if r.returncode:
        print(f"warning: grow failed; the disk still boots, run: sudo diskutil apfs resizeContainer {pdev} 0")
    disk.run("diskutil", "unmountDisk", target, check=False)
    print(f"done. {target} is ready; eject it and install in the Mac.")


# ---------------------------------------------------------------- push / pull (ssdpush-agent in the Recovery)

def _commit_manifest(repo, rev):
    """(manifest, meta, label) of a commit; HEAD when rev is None."""
    rev = rev or "HEAD"
    if not _has_head(repo):
        die("nothing committed yet")
    man, meta = repo.manifest(rev), repo.meta(rev)
    if not man:
        die("empty manifest")
    missing = [h for h in set(man.values()) if not repo.has(h)]
    if missing:
        die(f"{len(missing)} objects missing from store")
    return man, meta, repo.git("rev-parse", "--short", rev, capture=True).strip()


def _check_boot_image(image, name, label):
    """One acceptance path for committed images and captured, uncommitted builds."""
    print(f"boot check of {label} (volume name {name!r}):")
    problems = bootcheck.check_image(image, name)
    if problems:
        die(f"boot check FAILED ({len(problems)}): image refused; the target Mac would not boot it as intended or it "
            "still references an item our patches removed. Fix it in the build and check again")
    print("boot check: OK")


def run_bootcheck(repo, rev, name):
    """Check a committed manifest from the store; never read or rewrite the working image."""
    man, meta, label = _commit_manifest(repo, rev)
    scratch = tempfile.mkdtemp(prefix="ssdimg-revision-check-")
    image = os.path.join(scratch, "disk.img")
    try:
        print(f"materializing {label} in temporary storage ({human(meta['size'])} logical) ...")
        repo.materialize(man, meta["size"], path=image, chunk_size=meta["chunk_size"],
                         progress=Progress("restoring"))
        _check_boot_image(image, name, label)
    finally:
        # A failed hdiutil detach must not remove a still-attached image's backing file.
        if os.path.exists(image) and disk.attached_device(image):
            print(f"temporary image remains attached; kept {scratch}", file=sys.stderr)
        else:
            shutil.rmtree(scratch)


def cmd_check(a):
    repo = Repo.find(a.repo)
    if a.working:
        if not os.path.isfile(repo.work):
            die("no working image to check (capture first)")
        need_detached(repo)
        _check_boot_image(repo.work, a.name, "uncommitted working image")
    else:
        run_bootcheck(repo, a.rev, a.name)


def cmd_push(a):
    repo = Repo.find(a.repo)
    # Check and transfer the same immutable commit, even when a branch or tag moves meanwhile.
    try:
        revision = repo.git("rev-parse", "--verify", "--end-of-options",
                            f"{a.rev or 'HEAD'}^{{commit}}", capture=True).strip()
    except subprocess.CalledProcessError:
        die(f"invalid image revision: {a.rev or 'HEAD'}")
    if not a.no_check:
        run_bootcheck(repo, revision, a.name)
    man, meta, label = _commit_manifest(repo, revision)
    size, cs, sector = meta["size"], meta["chunk_size"], meta["sector_size"]
    print(f"image: {a.rev or 'HEAD'} ({label})  {human(size)} logical  ({len(man)} data chunks, {human(len(man) * cs)})")
    _g, keep = keep_ranges(repo.reader(man, size), size, sector)
    present = set(man) | set(chunk_masks(keep, size, cs))
    try:
        push.push(repo, man, meta, present, a)
    except (push.PushError, ValueError, OSError) as e:
        die(str(e))


def cmd_verify(a):
    repo = Repo.find(a.repo)
    man, meta, label = _commit_manifest(repo, a.rev)
    size, cs, sector = meta["size"], meta["chunk_size"], meta["sector_size"]
    print(f"image: {a.rev or 'HEAD'} ({label})  {human(size)} logical  ({len(man)} data chunks)")
    _g, keep = keep_ranges(repo.reader(man, size), size, sector)
    present = set(man) | set(chunk_masks(keep, size, cs))
    try:
        push.verify(repo, man, meta, present, a)
    except push.PushError as e:
        die(str(e))


def cmd_pull(a):
    repo = Repo.find(a.repo)
    need_detached(repo)
    if not a.force and not work_is_clean(repo):
        die("working image has uncommitted changes; commit them or pass --force to overwrite it")
    try:
        link = push.pull_open(a)
        tmp = repo.work + ".capture"
        size, sector, g = capture_core(a.target, tmp, repo.meta()["chunk_size"],
                                       reader=(push.RemoteReader(link, link.size), link.size, link.bsize))
        link.close()
    except push.PushError as e:
        die(str(e))
    os.replace(tmp, repo.work)
    m = repo.meta()
    m.update(size=size, sector_size=sector, source=a.target)
    repo.save_meta(m)
    with open(repo.layout_path, "w") as f:
        f.write(g.describe())
    print("pulled into the working image. next: `ssdimg fsck`, `ssdimg commit -m ...`")


# ---------------------------------------------------------------- image / write (one-shot front doors)

def _default_name(i):
    """Repository name derived from the drive's media name, e.g. SSD-850-EVO-250G."""
    return re.sub(r"[^A-Za-z0-9._-]+", "-", i.get("MediaName", "disk")).strip("-.") or "disk"


def cmd_image(a):
    """Guard the source disk, unmount it, capture into ./NAME, fsck, commit."""
    need_root("image")
    dev, i = disk.external_whole(a.disk)
    print(f"source: {disk.summary(i)}")
    fx = facts.probe(dev)  # model / macOS / OCLP from the drive's own plists
    if fx:
        print(f"contents: {facts.describe(fx)}")
    name = a.name or facts.repo_name(fx, _default_name(i))
    path = os.path.abspath(name)
    if os.path.exists(os.path.join(path, "image.json")):
        repo = Repo(path)
        print(f"repository: {path} (existing; this capture becomes a new commit)")
    elif os.path.isdir(path) and os.listdir(path):
        die(f"{path} exists and is not an ssdimg repository")
    else:
        repo = Repo.init(path)
        print(f"repository: {path} (new)")
    ok = True
    try:
        need_detached(repo)
        if not a.force and not work_is_clean(repo):
            die(f"the working image in {name} has uncommitted changes; "
                f"commit them (cd {name} && ssdimg commit -m ...) or pass --force")
        mounts = disk.mounted_under(dev)
        if mounts:
            print(f"unmounting {', '.join(mounts)} ...")
            disk.run("diskutil", "unmountDisk", dev)
        print()
        cmd_capture(argparse.Namespace(repo=path, source=dev, full=a.full, force=True))
        if fx:
            m = repo.meta()
            m["facts"] = fx
            repo.save_meta(m)
        print()
        ok = run_fsck(repo, quiet=True)
        what = facts.describe(fx) or i.get("MediaName", dev)
        msg = a.message or (f"Image of {what} from {i.get('MediaName', '?')} ({os.path.basename(dev)}) "
                            f"{time.strftime('%Y-%m-%d %H:%M')}")
        if not ok:
            msg += " [fsck: PROBLEMS FOUND]"
        print()
        cmd_commit(argparse.Namespace(repo=path, message=msg))
    finally:
        give_back_tree(path)
    print(f"\nimage stored in {path}")
    if not ok:
        print("WARNING: fsck reported problems with the captured image (was the drive cleanly unmounted?).\n"
              "It is committed so nothing is lost, but fix the source and re-run before writing it anywhere.")
        sys.exit(1)
    print(f"next: sudo ssdimg write {name} diskN   (or cd {name} for generalize / attach / log)")


def cmd_write(a):
    """Write NAME's committed image to an external disk, fitted to its size, verified."""
    need_root("write")
    path = os.path.abspath(a.name)
    if not os.path.exists(os.path.join(path, "image.json")):
        die(f"no ssdimg repository at {path}")
    dev, _ = disk.external_whole(a.disk)
    cmd_deploy(argparse.Namespace(repo=path, target=dev, rev=a.rev, verify=not a.no_verify, no_grow=False,
                                  yes=False, allow_internal=False, allow_virtual=False, strict=True, wipe=a.wipe))


# ---------------------------------------------------------------- main

def main(argv=None):
    ap = argparse.ArgumentParser(prog="ssdimg", description=__doc__)
    ap.add_argument("-C", dest="repo", default=None, help="repository directory (default: search upward from cwd)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("image", help="ONE SHOT: image an external disk into ./NAME (unmount, capture, fsck, commit)")
    s.add_argument("disk", help="external whole disk, e.g. disk7 or /dev/disk7")
    s.add_argument("name", nargs="?", help="repository directory to create or update in the current directory "
                   "(default: <model>-<macos>_v<ver>-oclp_v<ver> read from the drive, "
                   "e.g. macbookpro9,2-tahoe_v26.7.1-oclp_v3.0.0rc3)")
    s.add_argument("-m", "--message", help="commit message (default: model / macOS / OCLP, drive and date)")
    s.add_argument("--full", action="store_true", help="copy whole partitions instead of allocated APFS blocks")
    s.add_argument("--force", action="store_true", help="discard uncommitted changes in NAME's working image")
    s.set_defaults(fn=cmd_image)

    s = sub.add_parser("write", help="ONE SHOT: write NAME's committed image to an external disk (verify, grow)")
    s.add_argument("name", help="repository directory created by `ssdimg image`")
    s.add_argument("disk", help="external whole disk to overwrite, e.g. disk12 or /dev/disk12")
    s.add_argument("--rev", help="commit or tag to write (default: HEAD)")
    s.add_argument("--no-verify", action="store_true", help="skip the read-back comparison")
    s.add_argument("--no-trim", action="store_true",
                   help="write all-zero chunks as zeros (default: TRIM them; the drive reads zeros after TRIM, "
                        "and the verify pass checks that)")
    s.add_argument("--verify", choices=["inline", "end"],
                   help="end (default): write everything, then re-read every written chunk from the target and "
                        "hash-check it against the image; inline: read each batch back right after writing it")
    s.add_argument("--wipe", action="store_true", help="also write zeros over the target's free space")
    s.set_defaults(fn=cmd_write)

    s = sub.add_parser("init", help="create an image repository")
    s.add_argument("dir")
    s.add_argument("--chunk-size", default="262144", help="dedup chunk size in bytes (default 256 KiB)")
    s.set_defaults(fn=cmd_init)

    s = sub.add_parser("capture", help="image a disk (allocated APFS blocks only) into the working image")
    s.add_argument("source", help="/dev/diskN (whole disk, unmounted) or a raw image file")
    s.add_argument("--full", action="store_true", help="copy whole partitions instead of allocated blocks")
    s.add_argument("--force", action="store_true", help="overwrite an existing working image")
    s.set_defaults(fn=cmd_capture)

    s = sub.add_parser("scrub", help="zero free space in the working image (removes deleted-file remnants)")
    s.set_defaults(fn=cmd_scrub)

    s = sub.add_parser("shrink", help="shrink the repository's image itself (write fits to the target on its own)")
    s.add_argument("--size", help="container size, e.g. 60G (default: used + headroom)")
    s.add_argument("--headroom", default="8G", help="free space to leave when --size is omitted (default 8G)")
    s.set_defaults(fn=cmd_shrink)

    s = sub.add_parser("attach", help="attach + mount the working image for editing")
    s.add_argument("--readonly", action="store_true")
    s.add_argument("--owners", action="store_true", help="honour file ownership (needed for root-owned edits)")
    s.add_argument("--no-efi", action="store_true", help="don't mount the EFI partition")
    s.set_defaults(fn=cmd_attach)

    s = sub.add_parser("detach", help="unmount + detach the working image")
    s.add_argument("--force", action="store_true")
    s.set_defaults(fn=cmd_detach)

    s = sub.add_parser("fsck", help="verify EFI and APFS in the working image (read-only)")
    s.set_defaults(fn=cmd_fsck)

    s = sub.add_parser("generalize", help="remove user accounts/traces so the image boots to Setup Assistant")
    s.add_argument("--yes", action="store_true", help="apply (default is a dry run)")
    s.add_argument("--keep-user", action="append", help="account short name to keep (repeatable)")
    s.add_argument("--keep-logs", action="store_true", help="don't clear logs/caches/indexes")
    s.set_defaults(fn=cmd_generalize)

    s = sub.add_parser("status", help="compare the working image to HEAD")
    s.set_defaults(fn=cmd_status)

    s = sub.add_parser("commit", help="snapshot the working image")
    s.add_argument("-m", "--message", required=True)
    s.set_defaults(fn=cmd_commit)

    s = sub.add_parser("log", help="history")
    s.add_argument("-n", type=int)
    s.set_defaults(fn=cmd_log)

    s = sub.add_parser("checkout", help="restore working image to REV (keeps history; commit to record the undo)")
    s.add_argument("rev")
    s.add_argument("--force", action="store_true", help="discard uncommitted changes")
    s.set_defaults(fn=cmd_checkout)

    s = sub.add_parser("reset", help="move HEAD back to REV and restore image (drops later commits)")
    s.add_argument("rev")
    s.add_argument("--force", action="store_true")
    s.set_defaults(fn=cmd_reset)

    s = sub.add_parser("sync", help="rebuild working image from the git worktree manifest (after git checkout)")
    s.set_defaults(fn=cmd_sync)

    s = sub.add_parser("deploy", help="write an image to an SSD, fitted to its size (low-level form of `write`)")
    s.add_argument("target", help="/dev/diskN of the destination SSD")
    s.add_argument("--rev", help="commit to deploy (default: HEAD / clean working image)")
    s.add_argument("--verify", action="store_true", help="read back and compare after writing")
    s.add_argument("--no-grow", action="store_true", help="leave container at image size")
    s.add_argument("--wipe", action="store_true", help="also write zeros over the target's free space")
    s.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    s.add_argument("--allow-internal", action="store_true")
    s.add_argument("--allow-virtual", action="store_true")
    s.set_defaults(fn=cmd_deploy)

    s = sub.add_parser("push", help="stream a commit to the target Mac's internal disk from the Recovery it loads via "
                                    "NetBoot (ssdpush-agent over ssh)")
    s.add_argument("target", help="rec@ADDR:/dev/diskN (the Recovery at ADDR, the internal disk in it; netboot/status "
                                  "prints ADDR) or local:/path/file (tests)")
    s.add_argument("--rev", help="commit or tag to push (default: HEAD)")
    transfer = s.add_mutually_exclusive_group()
    transfer.add_argument("--delta", dest="delta", action="store_true", default=True,
                          help="hash the target first; send only differing chunks (default; full readback required)")
    transfer.add_argument("--full", dest="delta", action="store_false",
                          help="send all allocated chunks without an initial target comparison")
    s.add_argument("--no-verify", action="store_true", help="skip the read-back comparison (requires --full)")
    s.add_argument("--no-trim", action="store_true",
                   help="write all-zero chunks as zeros (default: TRIM them; the drive reads zeros after TRIM, "
                        "and the verify pass checks that)")
    s.add_argument("--verify", choices=["inline", "end"],
                   help="end (default): write, then re-read every allocated chunk from the target and "
                        "hash-check it against the image; inline: read each batch back right after writing it "
                        "(delta also performs a complete end readback)")
    s.add_argument("--yes", action="store_true", help="skip typing the target disk's identifier")
    s.add_argument("--keep-launcher", action="store_true",
                   help="leave OpenCore's LauncherOption as imaged (default: set Disabled so the firmware's default, "
                        "NetBoot, stays first)")
    s.add_argument("--name", default="macOS", help="expected System volume name for the boot check (default macOS)")
    s.add_argument("--no-check", action="store_true",
                   help="skip the pre-push boot check (ESP OpenCore, volume names, label, firmlinks, snapshot, "
                        "Dock tiles)")
    s.add_argument("--no-grow", action="store_true",
                   help="leave the APFS container at the image's size (default: grow it to the disk)")
    s.set_defaults(fn=cmd_push)

    s = sub.add_parser("check", help="read-only: would the target Mac boot this image as intended, and does nothing in it "
                                     "reference an item our patches removed? (pushes run it first)")
    source = s.add_mutually_exclusive_group()
    source.add_argument("--rev", help="commit or tag (default: HEAD); restored in temporary storage without checkout")
    source.add_argument("--working", action="store_true", help="check the captured working image before committing it")
    s.add_argument("--name", default="macOS", help="expected System volume name (default macOS)")
    s.set_defaults(fn=cmd_check)

    s = sub.add_parser("verify", help="read-only: check that the target Mac's internal disk holds a commit (re-read + "
                                      "hash every chunk)")
    s.add_argument("target", help="rec@ADDR:/dev/diskN or local:/path/file")
    s.add_argument("--rev", help="commit or tag (default: HEAD)")
    s.set_defaults(fn=cmd_verify)

    s = sub.add_parser("pull", help="capture the target Mac's internal disk into the working image (over ssh)")
    s.add_argument("target", help="rec@ADDR:/dev/diskN or local:/path/file")
    s.add_argument("--force", action="store_true", help="overwrite uncommitted changes in the working image")
    s.set_defaults(fn=cmd_pull)

    s = sub.add_parser("export", help="write a commit out as a standalone raw .img")
    s.add_argument("path")
    s.add_argument("--rev")
    s.add_argument("--part", type=int, help="write only GPT partition N (e.g. 1 = EFI) as a raw partition image")
    s.add_argument("--force", action="store_true")
    s.set_defaults(fn=cmd_export)

    s = sub.add_parser("bundle", help="write a commit as an image bundle (a folder) for the Recovery's Reinstall "
                                      "macOS to install from a USB disk (patches/rec-reinstall)")
    s.add_argument("path", help="new folder; on the USB disk it goes at the top level of a volume")
    s.add_argument("--rev")
    s.add_argument("--name", help="what Reinstall macOS shows (default: the revision)")
    s.add_argument("--model", help="IMAGE model=, e.g. MacBookPro9,2")
    s.add_argument("--macos-version", help="IMAGE macos_version=, e.g. 26.7")
    s.add_argument("--macos-build", help="IMAGE macos_build=, e.g. 25G332")
    s.add_argument("--segment-size", default="2G", help="stream file size limit (default 2G: FAT32-safe)")
    s.set_defaults(fn=cmd_bundle)

    s = sub.add_parser("bundle-check", help="read-only: re-read an image bundle and re-hash every chunk")
    s.add_argument("path")
    s.set_defaults(fn=cmd_bundle_check)

    s = sub.add_parser("gc", help="delete stored chunks no commit references")
    s.add_argument("--dry-run", action="store_true")
    s.set_defaults(fn=cmd_gc)

    s = sub.add_parser("info", help="repository summary")
    s.set_defaults(fn=cmd_info)

    a = ap.parse_args(argv)
    try:
        a.fn(a)
    except (StoreError, disk.DiskError, gpt.GPTError, apfs.ApfsError) as e:
        die(str(e))
    except KeyboardInterrupt:
        die("interrupted")
