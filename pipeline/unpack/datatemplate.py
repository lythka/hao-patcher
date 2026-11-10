#!/usr/bin/python3 -I
"""datatemplate.py --patterns Data.symlink-patterns --template TEMPLATE-DATA.tsv --receipt RECEIPT.txt
                   [--pristine-data PRISTINE-DATA.tsv]
expected pre-first-boot Data volume template = System/Library/Templates/Data with Data.symlink-patterns
applied (matched dirs/files become relative symlinks back into the template). Compare with the pristine receipt BOM
(com.apple.files.data-template.bom, written by OSInstaller 'templateMigrator') and with the pristine Data volume.
TEMPLATE-DATA.tsv = walk.py of tree/System/Library/Templates/Data; RECEIPT.txt = `lsbom -p fMUGsc
com.apple.files.data-template.bom`; PRISTINE-DATA.tsv = walk.py of a pristine Data volume. Prints the comparison."""
import argparse, collections, os, re
ap = argparse.ArgumentParser()
ap.add_argument("--patterns", required=True); ap.add_argument("--template", required=True)
ap.add_argument("--receipt", required=True); ap.add_argument("--pristine-data")
A = ap.parse_args()
# A trailing '/' marks a directory pattern (receipt plist SymlinkedDirectoryPatterns); the others match files only
# (SymlinkedFilePatterns). Matching both kinds against both types would turn the six `User Pictures/*` directories
# into symlinks.
pats = [l.strip() for l in open(A.patterns) if l.strip()]
def rx(p):  # '**' = anything incl '/', '*' = no '/'
    return re.compile('^' + re.escape(p.rstrip('/')).replace(r'\*\*', '.+').replace(r'\*', '[^/]*') + '$')
R = [('d' if p.endswith('/') else 'f', rx(p)) for p in pats]
T = {}
for l in open(A.template, encoding='utf-8', errors='surrogateescape'):
    f = l.rstrip('\n').split('\t'); T[f[0]] = f[1]
exp = {}; collapsed = set()
for p in sorted(T):
    if any(p.startswith(c + '/') for c in collapsed if c): continue
    if p != '.' and any(k == T[p] and r.match(p) for k, r in R):
        exp[p] = 'l'; collapsed.add(p) if T[p] == 'd' else None
    else: exp[p] = T[p]
rec = {}
for l in open(A.receipt, encoding='utf-8', errors='surrogateescape'):
    f = l.rstrip('\n').split('\t'); p = f[0][2:] if f[0] != '.' else '.'
    rec[p] = {'d': 'd', 'l': 'l', '-': 'f'}.get(f[1][0], f[1][0])
D = {}
for l in (open(A.pristine_data, encoding='utf-8', errors='surrogateescape') if A.pristine_data else ()):
    f = l.rstrip('\n').split('\t'); D[f[0]] = f[1]
def area(p, n=3): return '/'.join(p.split('/')[:n])
print('template entries', len(T), '-> expected after symlink patterns', len(exp), '(symlinks', sum(1 for v in exp.values() if v == 'l'), ')')
print('receipt BOM (written at install, pre-first-boot)', len(rec), 'symlinks', sum(1 for v in rec.values() if v == 'l'))
a, b = set(exp), set(rec)
print('expected vs receipt: both', len(a & b), 'type-diff', sum(1 for p in a & b if exp[p] != rec[p]), 'only-expected', len(a - b), 'only-receipt', len(b - a))
print('  only-expected', collections.Counter(area(p, 4) for p in a - b).most_common(6))
print('  only-receipt', collections.Counter(area(p, 4) for p in b - a).most_common(6))
c = set(D)
print('pristine Data volume', len(D), ': in receipt', len(c & b), 'receipt-entries-missing-on-volume', len(b - c), 'extra-on-volume', len(c - b))
print('  missing-on-volume', collections.Counter(area(p, 4) for p in b - c).most_common(8))
print('  extra-on-volume', collections.Counter(area(p, 3) for p in c - b).most_common(30))
