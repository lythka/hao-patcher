#!/usr/bin/python3 -I
"""fillin.py TSV NOOWNERS_MOUNT : for dirs marked unreadable (xattr ERR) in TSV, walk the same path on a noowners mount and append entries (uid/gid -1)."""
import os, sys, importlib.util
spec = importlib.util.spec_from_file_location('walk', os.path.join(os.path.dirname(__file__), 'walk.py')); w = importlib.util.module_from_spec(spec); spec.loader.exec_module(w)
tsv, M = sys.argv[1], sys.argv[2]
rows = [l.rstrip('\n').split('\t') for l in open(tsv, encoding='utf-8', errors='surrogateescape')]
have = set(r[0] for r in rows); dirs = [r[0] for r in rows if r[1] in ('d', 'ERR') and r[9] == 'ERR']
n = 0; err = 0
with open(tsv, 'a') as out:
    for d in dirs:
        for dp, dns, fns in os.walk(os.path.join(M, d), onerror=lambda e: None):
            for nm in dns + fns:
                rel = os.path.relpath(os.path.join(dp, nm), M)
                if rel in have: continue
                line = w.info((M, rel)).split('\t'); line[3] = line[4] = '-1'
                out.write('\t'.join(line) + '\n'); have.add(rel); n += 1
print('appended', n)
