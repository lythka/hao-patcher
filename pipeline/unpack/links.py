#!/usr/bin/python3 -I
"""Apply payloadv2 links.txt (=src / +dst hard links) under ROOT. Reports counts."""
import os, sys, json
root, links = sys.argv[1], sys.argv[2]
present = set(e['PAT'] for e in json.load(open(sys.argv[3]))) if len(sys.argv) > 3 else None
src = None; made = exist = missing = inarch = 0
for line in open(links, encoding='utf-8', errors='surrogateescape'):
    line = line.rstrip('\n')
    if not line: continue
    k, p = line[0], line[1:]
    if k == '=': src = p; continue
    if present is not None and p in present: inarch += 1
    s, d = os.path.join(root, src), os.path.join(root, p)
    if not os.path.lexists(s): missing += 1; print('MISSING SRC', src, file=sys.stderr); continue
    if os.path.lexists(d): exist += 1; continue
    os.makedirs(os.path.dirname(d), exist_ok=True)
    os.link(s, d, follow_symlinks=False); made += 1
print(f'links made={made} already_exist={exist} missing_src={missing} dst_also_in_archive={inarch}')
