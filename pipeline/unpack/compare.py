#!/usr/bin/python3 -I
"""compare.py: payload (archive metadata + extracted tree hashes + links.txt) vs a pristine System walk.

  compare.py --pristine PRISTINE-SYS.tsv [--pristine-unreadable-sha FILE] --payload PAYLOAD-SYS.tsv
             --payload-xattrs payload-xattrs.txt --payload-list payload-list.json --fixup-list fixup-list.json
             --links links.txt --details OUT.tsv
PRISTINE/PAYLOAD tsv = walk.py listings; payload-xattrs.txt = yaa.py output. Writes OUT.tsv (refuses to overwrite)
and prints a summary."""
import argparse, json, collections, sys, os
ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
for k in ("pristine", "payload", "payload-xattrs", "payload-list", "fixup-list", "links", "details"):
    ap.add_argument("--" + k, required=True)
ap.add_argument("--pristine-unreadable-sha")
A = ap.parse_args()
if os.path.exists(A.details):
    sys.exit("compare.py: %s exists; refusing to overwrite" % A.details)
def load_tsv(p):
    d = {}
    for line in open(p, encoding='utf-8', errors='surrogateescape'):
        f = line.rstrip('\n').split('\t')
        d[f[0]] = dict(t=f[1], mode=int(f[2], 8), uid=int(f[3]), gid=int(f[4]), flags=int(f[5], 16), size=int(f[6]), nlink=int(f[7]), val=f[8], xa=f[9])
    return d
pri = load_tsv(A.pristine)
for line in (open(A.pristine_unreadable_sha) if A.pristine_unreadable_sha else ()):
    r, h = line.rstrip('\n').split('\t')
    if not h.startswith('ERR'): pri[r]['val'] = h
tre = load_tsv(A.payload)
import ast
axat = {}
for l in open(A.payload_xattrs, encoding='utf-8', errors='surrogateescape'):
    pth, _, d = l.rstrip('\n').partition(' {'); axat[pth] = ';'.join(sorted(ast.literal_eval('{' + d)))
arch = {}
for e in json.load(open(A.payload_list)):
    arch[e['PAT'] or '.'] = e
# links.txt: + entries inherit the archive metadata of their source
src = None
for line in open(A.links, encoding='utf-8', errors='surrogateescape'):
    line = line.rstrip('\n')
    if line[:1] == '=': src = line[1:]
    elif line[:1] == '+': arch[line[1:]] = dict(arch[src], PAT=line[1:], _link=src)
FW = set()
for e in json.load(open(A.fixup_list)):
    if 'UID' in e and e['PAT'] not in arch:
        arch[e['PAT']] = dict(e, _src='firmlinks'); FW.add(e['PAT'])
TYP = {'F': 'f', 'D': 'd', 'L': 'l'}
UF_COMPRESSED = 0x20
res = collections.Counter(); cat = collections.defaultdict(collections.Counter); ex = collections.defaultdict(list)
def area(p):
    parts = p.split('/')
    return '/'.join(parts[:3]) if parts[0] == 'System' and len(parts) > 2 else '/'.join(parts[:2])
det = open(A.details, 'w')
allp = sorted(set(pri) | set(arch))
for p in allp:
    a, q = arch.get(p), pri.get(p)
    if a is None:
        res['only-in-pristine'] += 1; cat['only-in-pristine'][area(p)] += 1; det.write(f'only-pristine\t{p}\t{q["t"]}\n'); continue
    if q is None:
        res['only-in-payload'] += 1; cat['only-in-payload'][area(p)] += 1; det.write(f'only-payload\t{p}\t{TYP.get(a["TYP"])}\n'); continue
    t = tre.get(p)
    diffs = []
    if TYP.get(a['TYP']) != q['t']: diffs.append('type')
    else:
        if (a['MOD'] & 0o7777) != q['mode']: diffs.append('mode')
        if q['uid'] == -1: diffs.append('owner-unknown(pristine root-only dir)')
        elif a['UID'] != q['uid'] or a['GID'] != q['gid']: diffs.append('owner')
        af, qf = a['FLG'], q['flags']
        if (af & ~UF_COMPRESSED) != (qf & ~UF_COMPRESSED): diffs.append('flags')
        elif (af & UF_COMPRESSED) != (qf & UF_COMPRESSED): diffs.append('afsc-flag')
        if q['t'] == 'f':
            if q['val'].startswith('ERR'): diffs.append('pristine-unreadable')
            elif t is None or t['val'] != q['val']:
                diffs.append('content' if (t and t['size'] != q['size']) else 'content-samesize')
            if t and t['nlink'] != q['nlink'] and q['uid'] != -1: diffs.append('nlink')
        if q['t'] == 'l' and a.get('LNK') != q['val']: diffs.append('symlink')
        pa = axat.get(p, axat.get(a.get('_link'), '')) if p not in FW else 'com.apple.fs.firmlink' + (';com.apple.rootless' if a.get('XAT', 0) > 40 else '')
        if q['xa'] != 'ERR' and pa != q['xa']: diffs.append('xattr')
    if not diffs: res['identical'] += 1
    else:
        res['differing'] += 1
        for d in diffs:
            cat[d][area(p)] += 1
            if len(ex[d]) < 12: ex[d].append(p)
        det.write('differ\t%s\t%s\t%s\n' % (p, ','.join(diffs), json.dumps(dict(arch={k: a.get(k) for k in ('MOD','UID','GID','FLG','LNK')}, pri={k: q[k] for k in ('mode','uid','gid','flags','size','xa')}, tree_xa=t['xa'] if t else None, tree_size=t['size'] if t else None))))
print('TOTAL paths: pristine', len(pri), 'payload(archive+links)', len(arch))
for k, v in res.items(): print(f'{k:20s} {v}')
for d, c in sorted(cat.items(), key=lambda x: -sum(x[1].values())):
    print(f'\n## {d}: {sum(c.values())}')
    for k, v in c.most_common(15): print(f'   {v:7d}  {k}')
    for e in ex.get(d, [])[:6]: print('     e.g.', e)
