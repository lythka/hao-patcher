"""Removed-item references in layouts and our shipped data (read-only).

Rule: anything removed from the image (for example iPhone Mirroring.app, which otherwise leaves a "?" Dock tile) must
also be removed from every place that references it, and the build must refuse an image
where a removed item is still referenced within the L1-L4 scope below. `ssdimg check` (every build-image run, every push) runs these rules through
bootcheck.check_image; `python3 refcheck.py --sys DIR --data DIR` runs them on any two folders (simulations, verify.sh).

  L1  Dock's default.plist: every tile under /System (persistent-apps, persistent-others) exists on the System volume.
      /System/Cryptexes/App/... counts as present when Preboot holds the App cryptex (cryptex1/current/app.dmg), since it
      is grafted at boot. Needs no record: it catches any removal that forgot the default Dock. (Tiles under
      /Applications may be missing: Apple lists optional apps there, e.g. Keynote Creator Studio, and the Dock skips them.)
  L2  The removal records are complete: every top-level bundle (.app, .appex, .prefPane) that a patchset
      outputs/*.tsv row marks `removed` has a row in its module's removes.tsv.
  L3  For each removes.tsv item that is absent from the image: no plist in Dock.app/Contents/Resources holds its path,
      its file URL / %20 name, or its bundle id (parsed; exact strings and dict keys, and strings containing the path).
  L4  For each removes.tsv item that is absent from the image: no file that our data layer puts on the Data volume
      (every volume=data row of any patchset outputs/*.tsv that is a file on the image: seeds, templates, caches,
      prefs), plus every actual file in the generated LS/IS/shared-store trees, contains its bundle file name
      (UTF-8, %20-encoded, UTF-16) or its bundle id as a whole word (bytes).
Scope: invisible Apple-internal references are excluded; user-visible entries are included even when Apple authored
them. L3 also refuses iPhone_Mirroring entries in Settings searchTerms resources.
The Dictionary definition explicitly stays. Dock layouts and our shipped/generated
seeds, caches and preferences remain checked even when copied from Apple. Static
resource checks do not replace fresh-account Spotlight acceptance.
An item that is still on the image (its part not built in this channel) is reported and not checked.
Stdlib only; python 3.9+ (runs under ssdimg's 3.14 and /usr/bin/python3).
"""
import csv
import glob
import os
import plistlib
import re
import stat
import sys

PATCHSET = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "patchset")
DOCK_RES = "System/Library/CoreServices/Dock.app/Contents/Resources"
BUNDLE_SUFFIXES = (".app", ".appex", ".prefPane")
CRYPTEX_APP = "/System/Cryptexes/App/"
SEED_TREES = (
    "Library/Application Support/PerUserSeed/ls",
    "Library/Application Support/PerUserSeed/is",
    "Library/Caches/com.apple.iconservices.store",
    "private/var/folders/zz/zyxvpxvq6csfxvn_n0000000000000/C/com.apple.iconservices",
    "private/var/folders/zz/zyxvpxvq6csfxvn_n00000z000007r/C/com.apple.iconservices",
)


def seed_files(datadir):
    """Discover generated filenames on the image itself; never follow seed-tree links.

    Per-build blob names need not occur in historical outputs.tsv. This is only
    plaintext reference coverage, not a proof of semantic index/blob integrity.
    """
    files, errors = set(), []
    try:
        device = os.stat(datadir).st_dev
    except OSError as error:
        return files, [str(error)]
    for relative in SEED_TREES:
        path = datadir
        traversing = False
        try:
            for component in relative.split('/'):
                path = os.path.join(path, component)
                info = os.lstat(path)
                if not stat.S_ISDIR(info.st_mode) or info.st_dev != device:
                    raise ValueError('seed-tree ancestor is not a real directory: ' + path)
            traversing = True
            def onerror(error):
                errors.append(str(error))
            for directory, dirs, names in os.walk(path, followlinks=False, onerror=onerror):
                for name in list(dirs) + names:
                    entry = os.path.join(directory, name)
                    info = os.lstat(entry)
                    if info.st_dev != device or not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
                        errors.append('unsupported link/type/mount in seed tree: ' + entry)
                        if name in dirs:
                            dirs.remove(name)
                    elif stat.S_ISREG(info.st_mode):
                        files.add(os.path.relpath(entry, datadir))
        except FileNotFoundError as error:
            # A channel without a seed tree is checked by its output/selection
            # verifier. Reference scanning does not require a missing tree.
            if traversing:
                errors.append(str(error))
            continue
        except (OSError, ValueError) as error:
            errors.append(str(error))
    return files, errors


def open_seed_file(datadir, relative):
    """Open each component relative to its checked directory; no symlink escape."""
    parts = relative.split('/')
    if any(part in ('', '.', '..') for part in parts):
        raise OSError('unsafe generated seed path')
    directory = os.open(datadir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    device = os.fstat(directory).st_dev
    try:
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = child
            if os.fstat(directory).st_dev != device:
                raise OSError('generated seed path crosses a mount')
        descriptor = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_dev != device:
            os.close(descriptor)
            raise OSError('generated seed file type/device changed')
        return os.fdopen(descriptor, 'rb')
    finally:
        os.close(directory)


def _rows(path):
    """TSV rows as dicts; '#' lines skipped; the first other line is the header"""
    with open(path, newline="") as f:
        lines = [l for l in f if l.strip() and not l.startswith("#")]
    return list(csv.DictReader(lines, delimiter="\t", quoting=csv.QUOTE_NONE))


def load_removals(patchset=PATCHSET):
    """-> [item]: dict(part, volume, path, bundle_id, name, module, file)"""
    items = []
    for f in sorted(glob.glob(os.path.join(patchset, "*", "*", "removes.tsv"))):
        for r in _rows(f):
            r = {k: (v or "").strip() for k, v in r.items()}
            r["module"] = os.path.basename(os.path.dirname(f))
            r["file"] = os.path.relpath(f, patchset)
            items.append(r)
    return items


def load_outputs(patchset=PATCHSET):
    """-> [(module, tsv relpath, volume, path, change)] from every patchset/<layer>/<module>/outputs/*.tsv"""
    out = []
    for f in sorted(glob.glob(os.path.join(patchset, "*", "*", "outputs", "*.tsv"))):
        mod = os.path.basename(os.path.dirname(os.path.dirname(f)))
        for r in _rows(f):
            if r.get("volume") and r.get("path"):
                out.append((mod, os.path.relpath(f, patchset), r["volume"], r["path"], r.get("change") or ""))
    return out


def _walk(o):
    """every string (values and dict keys) in a parsed plist"""
    if isinstance(o, str):
        yield o
    elif isinstance(o, dict):
        for k, v in o.items():
            yield k
            yield from _walk(v)
    elif isinstance(o, list):
        for v in o:
            yield from _walk(v)


def _tiles(d):
    for key in ("persistent-apps", "persistent-others"):
        for t in d.get(key, []) or []:
            try:
                u = t["tile-data"]["file-data"]["_CFURLString"]
            except (KeyError, TypeError):
                continue
            if isinstance(u, str):
                yield key, u


def _url_path(u):
    if u.startswith("file://"):
        u = re.sub(r"%([0-9A-Fa-f]{2})", lambda m: chr(int(m.group(1), 16)), u[len("file://"):])
    return u.rstrip("/")


def _needles(item):
    """-> (plain strings for parsed plists, matcher(bytes) -> bool for raw files)"""
    base = os.path.basename(item["path"].rstrip("/"))
    bid = item.get("bundle_id") if item.get("bundle_id") not in ("", "-", None) else None
    plain = ["/" + item["path"].strip("/"), base.replace(" ", "%20")] + ([bid] if bid else [])
    subs = [base.encode(), base.replace(" ", "%20").encode(), base.encode("utf-16-le"), base.encode("utf-16-be")]
    word = re.compile(re.escape(bid.encode()) + rb"(?![A-Za-z0-9_.-])") if bid else None

    def match(b):
        if any(x in b for x in subs):     # memmem; the regex only where the id's bytes occur at all
            return True
        return bool(word and bid.encode() in b and word.search(b))
    return plain, match


def check(sysdir, datadir, report, preboot=None, patchset=PATCHSET):
    """Run L1-L4. `report(ok, text)` is called per line (bootcheck's check()). Returns the number of FAIL lines."""
    fails = [0]

    def rep(ok, text):
        report(ok, text)
        if not ok:
            fails[0] += 1

    def info(text):
        print(f"  info {text}")

    dock = os.path.join(sysdir, DOCK_RES)
    # L1
    dp = os.path.join(dock, "default.plist")
    try:
        with open(dp, "rb") as stream:
            d = plistlib.load(stream)
    except (OSError, plistlib.InvalidFileException, ValueError) as e:
        rep(False, f"refs L1: Dock default.plist readable ({e})")
        d = None
    if d is not None:
        n, missing, cryptex = 0, [], []
        for key, u in _tiles(d):
            p = _url_path(u)
            if not p.startswith("/System/"):
                continue
            n += 1
            if p.startswith(CRYPTEX_APP):
                if preboot and os.path.isfile(os.path.join(preboot, "cryptex1/current/app.dmg")):
                    continue
                if not preboot:
                    cryptex.append(p)
                    continue
                missing.append(f"{p} ({key}; no App cryptex on Preboot)")
            elif not os.path.lexists(sysdir + p):
                missing.append(f"{p} ({key})")
        for m in missing:
            rep(False, f"refs L1: Dock default.plist tile {m} is not on the System volume")
        if not missing:
            rep(True, f"refs L1: Dock default.plist: all {n} tiles under /System exist"
                + (f" ({len(cryptex)} App-cryptex tile(s) not checked: no Preboot given)" if cryptex else ""))

    # records (L2-L4): only when a patch set with removal records is given; the image pipeline passes None
    if patchset is None:
        return fails[0]
    if not os.path.isdir(patchset):
        rep(False, f"refs: patch set records at {patchset} readable")
        return fails[0]
    items, outs = load_removals(patchset), load_outputs(patchset)
    # L2
    recorded = {(i["volume"], i["path"].strip("/")) for i in items}
    removed = {(v, p.strip("/")): (mod, f) for mod, f, v, p, ch in outs if ch == "removed"}
    tops = [k for k in removed if k[1].endswith(BUNDLE_SUFFIXES)
            and not any(k[1].startswith(o[1] + "/") for o in removed if o[0] == k[0] and o[1].endswith(BUNDLE_SUFFIXES))]
    unrec = sorted(k for k in tops if k not in recorded)
    for v, p in unrec:
        rep(False, f"refs L2: {removed[(v, p)][1]} removes {v}:{p} but no removes.tsv row records it "
                   f"(add it to patchset/.../{removed[(v, p)][0]}/removes.tsv)")
    if not unrec:
        rep(True, f"refs L2: removal records complete ({len(tops)} removed bundle(s) in outputs, {len(items)} removes.tsv row(s))")

    roots = {"sys": sysdir, "data": datadir}
    live = []
    for it in items:
        root = roots.get(it["volume"])
        if root is None:
            rep(False, f"refs: {it['file']}: unknown volume {it['volume']!r}")
            continue
        if os.path.lexists(os.path.join(root, it["path"])):
            info(f"refs: {it['volume']}:{it['path']} is on this image ({it['module']}/{it['part']} not built): not checked")
        else:
            live.append(it)
    if not live:
        return fails[0]

    # L3: Dock.app resource plists
    plists = sorted(glob.glob(os.path.join(dock, "*.plist")))
    for it in live:
        plain, _ = _needles(it)
        bad = []
        for p in plists:
            try:
                with open(p, "rb") as stream:
                    v = plistlib.load(stream)
            except Exception as e:  # noqa: BLE001 - an unreadable plist is a finding
                rep(False, f"refs L3: {os.path.relpath(p, sysdir)} parses ({e})")
                continue
            hits = sorted({s for s in _walk(v) if s in plain or plain[0] in s or plain[1] in s})
            if hits:
                bad.append((os.path.basename(p), hits))
        for name, hits in bad:
            rep(False, f"refs L3: {DOCK_RES}/{name} still references removed {it['name']} ({', '.join(hits)})")
        if not bad:
            rep(True, f"refs L3: {len(plists)} Dock.app plists: no reference to removed {it['name']} "
                      f"({it['path']}, {it['bundle_id']})")

    # Settings search is user-visible, unlike inert Apple binary/jetsam references.
    if any(it.get('bundle_id') == 'com.apple.ScreenContinuity' for it in live):
        prefix = 'System/Library/ExtensionKit/Extensions/AirDropHandoffExtension.appex/Contents/Resources/'
        expected = {p for _, _, volume, p, _ in outs
                    if volume == 'sys' and p.startswith(prefix) and p.endswith('/AirDropHandoff.searchTerms')}
        actual = {os.path.relpath(p, sysdir) for p in glob.glob(os.path.join(sysdir, prefix, '*.lproj/AirDropHandoff.searchTerms'))}
        if not actual:
            rep(False, 'refs L3: Settings search resource set missing')
        for relative in sorted(expected | actual):
            path = os.path.join(sysdir, relative)
            try:
                parents = [path, os.path.dirname(path), os.path.dirname(os.path.dirname(path))]
                if any(os.path.islink(p) for p in parents):
                    raise ValueError('symlink Settings resource')
                with open(path, 'rb') as stream: values = plistlib.load(stream)
                if not isinstance(values, dict): raise ValueError('Settings resource is not a dictionary')
                present = any(v == 'iPhone_Mirroring' for v in _walk(values))
                rep(not present, f'refs L3: Settings search {relative}: '
                    + ('removed feature entry survives' if present else 'no iPhone_Mirroring entry'))
            except Exception as error:
                rep(False, f'refs L3: Settings search {relative} unreadable/invalid ({error})')

    # L4: what our data layer puts on the Data volume
    generated, errors = seed_files(datadir)
    for error in errors:
        rep(False, f"refs L4: generated seed tree could not be fully scanned ({error})")
    files = sorted({p.strip("/") for mod, f, v, p, ch in outs if v == "data" and ch not in ("removed", "cleared")} | generated)
    present, unreadable, scanned = [], [], 0
    for p in files:
        fp = os.path.join(datadir, p)
        if os.path.islink(fp) or not os.path.isfile(fp):
            if p in generated:
                rep(False, f"refs L4: generated seed file disappeared or changed type: {p}")
            continue
        present.append(p)
    hits = {id(it): [] for it in live}
    pats = [(it, _needles(it)[1]) for it in live]
    CHUNK, OVER = 64 << 20, 4096          # read in 64 MiB pieces, each overlapping the last by 4 KiB
    for p in present:
        found = set()
        try:
            with (open_seed_file(datadir, p) if p in generated else open(os.path.join(datadir, p), "rb")) as fh:
                tail = b""
                while True:
                    piece = fh.read(CHUNK)
                    if not piece:
                        break
                    scanned += len(piece)
                    b = tail + piece
                    for it, rx in pats:
                        if id(it) not in found and rx(b):
                            found.add(id(it))
                    tail = b[-OVER:]
        except OSError:
            unreadable.append(p)
            if p in generated:
                rep(False, f"refs L4: generated seed file is unreadable or changed: {p}")
            continue
        for it, rx in pats:
            if id(it) in found:
                hits[id(it)].append(p)
    if unreadable:
        info(f"refs L4: {len(unreadable)} data-layer file(s) not readable on this mount, not scanned "
             f"(e.g. {unreadable[0]})")
    for it in live:
        for p in hits[id(it)]:
            rep(False, f"refs L4: Data {p} still references removed {it['name']} ({it['path']}, {it['bundle_id']})")
        if not hits[id(it)]:
            rep(True, f"refs L4: {len(present) - len(unreadable)} data-layer files on Data ({scanned // 1048576} MiB): "
                      f"no reference to removed {it['name']}")
    return fails[0]


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sys", required=True, help="System volume root (mounted or a tree)")
    ap.add_argument("--data", required=True, help="Data volume root (mounted or a tree)")
    ap.add_argument("--preboot", help="Preboot <group> folder (for /System/Cryptexes/App tiles)")
    ap.add_argument("--patchset", default=PATCHSET)
    a = ap.parse_args(argv)
    n = check(a.sys, a.data, lambda ok, t: print(f"  {'ok  ' if ok else 'FAIL'} {t}"), a.preboot, a.patchset)
    print(f"refcheck: {'FAILED (' + str(n) + ')' if n else 'OK'}")
    return 1 if n else 0


if __name__ == "__main__":
    sys.exit(main())
