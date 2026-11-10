#!/usr/bin/python3 -I
"""bomcheck.py: assembled System tree (payload archive + links.txt + firmlinks) vs Apple's post.bom (the final System
volume of the same build). Checks the path set, type, mode, uid/gid (from the archive metadata), and for files size
and POSIX cksum CRC (from the extracted tree).

  bomcheck.py --post-bom POST-BOM.txt --tree-cksum TREE-CKSUM.txt --payload-list PAYLOAD-LIST.json
              --fixup-list FIXUP-LIST.json --links links.txt [--json OUT.json]

POST-BOM.txt = `lsbom -p fMUGsc AssetData/post.bom`; TREE-CKSUM.txt = `cd tree && find . -type f -print0 | xargs -0
cksum`; PAYLOAD-LIST.json / FIXUP-LIST.json = `aa list -list-format json` of payload.yaa and of the pbzx-decoded
fixup.manifest. Exit 0 when every post.bom entry matches and no entry is extra, else 1."""
import argparse, collections, grp, json, pwd, stat, sys

ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
for k in ("post-bom", "tree-cksum", "payload-list", "fixup-list", "links"):
    ap.add_argument("--" + k, required=True)
ap.add_argument("--json")
a = ap.parse_args()


def uid(n):
    try: return pwd.getpwnam(n).pw_uid
    except KeyError: return int(n) if n.isdigit() else n


def gid(n):
    try: return grp.getgrnam(n).gr_gid
    except KeyError: return int(n) if n.isdigit() else n


bom = {}
for l in open(a.post_bom, encoding='utf-8', errors='surrogateescape'):
    f = l.rstrip('\n').split('\t')
    p = '.' if f[0] == '.' else f[0][2:]
    bom[p] = dict(mode=f[1].strip(), uid=f[2], gid=f[3], size=f[4], ck=f[5] if len(f) > 5 else '')
ck = {}
for l in open(a.tree_cksum, encoding='utf-8', errors='surrogateescape'):
    c, s, p = l.rstrip('\n').split(' ', 2); ck[p[2:]] = (c, s)
arch = {}
for e in json.load(open(a.payload_list)): arch[e['PAT'] or '.'] = e
for e in json.load(open(a.fixup_list)):
    if 'UID' in e and (e['PAT'] or '.') not in arch: arch[e['PAT'] or '.'] = dict(e, _src='firmlinks/fixup')
src = None
for line in open(a.links, encoding='utf-8', errors='surrogateescape'):
    line = line.rstrip('\n')
    if line[:1] == '=': src = line[1:]
    elif line[:1] == '+': arch[line[1:]] = dict(arch[src], _link=src)
T = {'F': '-', 'D': 'd', 'L': 'l'}
r = collections.Counter(); ex = collections.defaultdict(list)


def add(k, p):
    r[k] += 1
    if len(ex[k]) < 8: ex[k].append(p)


for p, b in bom.items():
    e = arch.get(p)
    if e is None: add('bom-only', p); continue
    if T[e['TYP']] != b['mode'][0]: add('type', p); continue
    if stat.filemode(e['MOD'])[1:] != b['mode'][1:10] and not (b['mode'][1:10].upper() == stat.filemode(e['MOD'])[1:].upper()): add('mode', p)
    if uid(b['uid']) != e['UID'] or gid(b['gid']) != e['GID']: add('owner', p)
    if b['mode'][0] == '-':
        c = ck.get(p)
        if c is None: add('file-missing-in-tree', p)
        elif c[1] != b['size']: add('size', p)
        elif c[0] != b['ck']: add('crc', p)
        else: r['file-size+crc-match'] += 1
    r['entries-matched'] += 1
for p in arch:
    if p not in bom: add('archive-only', p)
print('post.bom entries', len(bom), 'assembled entries', len(arch))
for k, v in sorted(r.items()): print(f'  {k:24s} {v}')
for k, v in ex.items(): print(' ', k, v)
bad = sum(v for k, v in r.items() if k not in ('entries-matched', 'file-size+crc-match'))
files = sum(1 for b in bom.values() if b['mode'][0] == '-')
ok = not bad and r['file-size+crc-match'] == files
if a.json:
    json.dump({"post_bom_entries": len(bom), "assembled_entries": len(arch), "bom_files": files, "counts": dict(r),
               "examples": dict(ex), "ok": ok}, open(a.json, "w"), indent=1)
print("OK" if ok else "FAIL")
sys.exit(0 if ok else 1)
