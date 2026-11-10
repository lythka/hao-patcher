#!/usr/bin/python3 -I
"""mklist.py UNPACK_OUT OUTDIR : build the hybrid metadata lists for hmeta from Apple's own archives.
  sys.tsv  = every fixup.manifest entry (Apple's final System manifest: UID/GID/MOD/FLG/XAT), mtime (exact s+ns)
             from payload.yaa or firmlinks_payload (the 17 firmlink dirs); hard-link entries (HLC/HLO, no
             metadata) as typ H.
  data.tsv = the Templates/Data entries re-rooted at the Data volume, after the Data.symlink-patterns collapse
             (matched paths become symlinks with the replaced entry's owner/mode/flags, no mtime: made at install).
  data-symlinks.tsv = path<TAB>target for the host-side symlink step.
Format: path typ uid gid mode(octal) flags(hex) sec nsec xattrs(name=hex,...|-)"""
import os, re, struct, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "unpack"))
import yaa  # noqa: E402  (pipeline/unpack/yaa.py)

out, dst = sys.argv[1], sys.argv[2]
PV2 = os.path.join(out, "zip/AssetData/payloadv2/")
TPL = "System/Library/Templates/Data"


def pat(f):
    p = f.get("PAT", b"")
    return (p.decode("utf-8", "surrogateescape") if isinstance(p, bytes) else p) or "."


def mtimes(path):
    m = {}
    with open(path, "rb") as fh:
        for f, d in yaa.entries(fh):
            t = f.get("MTM")
            if isinstance(t, bytes) and len(t) == 12:    # YAA 'T': int64 sec + uint32 nsec
                s, ns = struct.unpack("<qI", t); m[pat(f)] = (s, ns)
            elif isinstance(t, bytes) and len(t) == 8:   # YAA 'S': int64 sec only
                m[pat(f)] = (struct.unpack("<q", t)[0], 0)
    return m


MT = mtimes(os.path.join(out, "payload.yaa"))
MT_FL = mtimes(PV2 + "firmlinks_payload")
print("mtimes: payload", len(MT), "firmlinks_payload", len(MT_FL), file=sys.stderr)

rows, typ_of = [], {}
with open(os.path.join(out, "fixup.yaa"), "rb") as fh:
    for f, d in yaa.entries(fh):
        p = pat(f)
        if "\t" in p or "\n" in p:
            raise SystemExit("path with tab/newline: %r" % p)
        t = chr(f["TYP"]) if isinstance(f.get("TYP"), int) else "?"
        if "UID" not in f and "HLC" in f:
            rows.append((p, "H", 0, 0, 0, 0, None, "-")); continue
        xs = "-"
        if "XAT" in d and d["XAT"]:
            xs = ",".join("%s=%s" % (k, v.hex()) for k, v in yaa.xattrs(d["XAT"]).items())
        mt = MT.get(p) or MT_FL.get(p)
        rows.append((p, t, f["UID"], f["GID"], f["MOD"] & 0o7777, f.get("FLG", 0), mt, xs)); typ_of[p] = t


def line(r):
    p, t, u, g, m, fl, mt, xs = r
    return "%s\t%s\t%d\t%d\t%o\t%x\t%s\t%s\t%s\n" % (p, t, u, g, m, fl, mt[0] if mt else "-", mt[1] if mt else "-", xs)


os.makedirs(dst, exist_ok=True)
with open(os.path.join(dst, "sys.tsv"), "w", encoding="utf-8", errors="surrogateescape") as o:
    for r in rows: o.write(line(r))
nomt = sum(1 for r in rows if r[1] != "H" and not r[6])
print("sys.tsv", len(rows), "entries; hard-link rows", sum(1 for r in rows if r[1] == "H"), "; without mtime", nomt,
      "; with xattrs", sum(1 for r in rows if r[7] != "-"), file=sys.stderr)

# Data template (same pattern rules as unpack/datatemplate.py)
pats = []
for l in open(os.path.join(os.environ.get("HYB_TREE", ""), TPL + ".symlink-patterns")):
    q = l.strip()
    if q:
        isdir = q.endswith("/"); q = q.rstrip("/")
        pats.append((re.compile("^" + re.escape(q).replace(r"\*\*", ".+").replace(r"\*", "[^/]*") + "$"), isdir))
tpl = [r for r in rows if r[0].startswith(TPL + "/")]
links, coll, drows = [], [], []
for r in sorted(tpl):
    rel = r[0][len(TPL) + 1:]
    if any(rel.startswith(c + "/") for c in coll): continue
    t = r[1]
    hit = None
    for rx, isdir in pats:
        if rx.match(rel) and ((isdir and t == "D") or (not isdir and t == "F")):
            hit = isdir; break
    if hit is not None:
        links.append((rel, "../" * rel.count("/") + TPL + "/" + rel + ("/" if hit else "")))
        if hit: coll.append(rel)
        # As on a pristine Data volume, the symlink keeps the replaced entry's owner, mode and flags
        drows.append((rel, "L") + tuple(r[2:6]) + (None, "-"))
    else:
        drows.append((rel,) + r[1:])
with open(os.path.join(dst, "data.tsv"), "w", encoding="utf-8", errors="surrogateescape") as o:
    for r in drows: o.write(line(r))
with open(os.path.join(dst, "data-symlinks.tsv"), "w", encoding="utf-8", errors="surrogateescape") as o:
    for p, tg in links: o.write("%s\t%s\n" % (p, tg))
print("data.tsv", len(drows), "entries; symlinks", len(links), "(dirs", len(coll), ")", file=sys.stderr)
