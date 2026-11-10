#!/usr/bin/python3 -I
"""stage-pb.py MAP UNPACK_OUT SYSROOT PBROOT RCROOT GROUP: fill Preboot/<GROUP> and Recovery/<GROUP> as the user.
MAP = the build's preboot-recovery-map.tsv (each Preboot/Recovery file of a real install -> the unpack output with the
same sha256; rows keyed by the reference install's group REFG). Sources: zip/ and out/ under UNPACK_OUT, tree/ = SYSROOT.
Generated here: cryptex1/current/BuildManifest.plist and restore/BuildManifest.plist (= the zip's BuildManifest minus the
'Customer Software Update' identities, plistlib XML: byte-identical to an install), os.clone.dmg =
APFS clone of os.dmg. Not made (install/first-boot state, or the guest's job): PreLoginData, .disk_label*, .root_uuid,
EFILoginLocalizations, wipekey, Library/Preferences, var/db/ProgressMarkers. Copies keep the source mtime.
Prints dst<TAB>src per file."""
import os, plistlib, shutil, subprocess, sys
MAP, OUT, SYS, PB, RC, G = sys.argv[1:7]
REFG = "627D437A-0E17-433B-9934-94467FC14137"
def src_path(s):
    top, rest = s.split("/", 1)
    return os.path.join(SYS, rest) if top == "tree" else os.path.join(OUT, s)
n = {"preboot": 0, "recovery": 0}; skipped = []
clone = []
for l in open(MAP):
    vol, rel, size, h, kind, srcs = l.rstrip("\n").split("\t")
    assert rel.startswith(REFG + "/"), rel
    dst = os.path.join(PB if vol == "preboot" else RC, G, rel[len(REFG) + 1:])
    if kind != "matched":
        skipped.append((vol, rel, kind)); continue
    if rel.endswith("cryptex1/current/os.clone.dmg"):
        clone.append(dst); continue
    s = src_path(srcs.split(";")[0])
    if os.path.getsize(s) != int(size):
        raise SystemExit("size mismatch %s %s" % (s, size))
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copy2(s, dst); n[vol] += 1
    print("%s\t%s" % (os.path.relpath(dst, os.path.dirname(PB if vol == "preboot" else RC)), os.path.relpath(s, OUT) if s.startswith(OUT) else "SYS:" + os.path.relpath(s, SYS)))
for dst in clone:
    subprocess.check_call(["/bin/cp", "-c", "-p", os.path.join(os.path.dirname(dst), "os.dmg"), dst]); n["preboot"] += 1
    print("%s\tclone of os.dmg" % dst)
bm = plistlib.load(open(os.path.join(OUT, "zip/AssetData/boot/BuildManifest.plist"), "rb"))
bm["BuildIdentities"] = [i for i in bm["BuildIdentities"] if i["Info"].get("Variant") != "Customer Software Update"]
for d in ("cryptex1/current/BuildManifest.plist", "restore/BuildManifest.plist"):
    p = os.path.join(PB, G, d); os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as f: plistlib.dump(bm, f, fmt=plistlib.FMT_XML, sort_keys=True)
    n["preboot"] += 1; print("%s\tBuildManifest trimmed (%d identities)" % (d, len(bm["BuildIdentities"])))
print("staged", n, "skipped", len(skipped), file=sys.stderr)
for s in skipped:
    if "PreLoginData" not in s[1]: print("skipped\t%s\t%s\t%s" % s, file=sys.stderr)
