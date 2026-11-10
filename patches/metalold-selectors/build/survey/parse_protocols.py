"""Parse `ipsw dyld macho CACHE IMAGE --objc` output into {protocol: {parents, methods:[[kind, sel, required]]}}."""
import json
import re
import sys

src, out = sys.argv[1:3]
protocols = {}
classes = {}
cur = None
curclass = None
chead = re.compile(r'^@interface\s+(\w+)\s*:\s*(\w*)')
required = True
head = re.compile(r'^@protocol\s+(\w+)(?:\s*<([^>]*)>)?')
meth = re.compile(r'^([-+])\[(\w+)\s+([^\]]+)\];')
for line in open(src, encoding='utf-8', errors='replace'):
    line = line.rstrip('\n')
    m = head.match(line)
    if m:
        cur = m.group(1)
        parents = [p.strip() for p in (m.group(2) or '').split(',') if p.strip()]
        protocols[cur] = {'parents': parents, 'methods': []}
        required = True
        continue
    c = chead.match(line)
    if c:
        curclass = c.group(1)
        classes.setdefault(curclass, {'super': c.group(2), 'methods': []})
        continue
    if line.startswith('@end'):
        cur = curclass = None
        continue
    m = meth.match(line)
    if curclass and m and m.group(2) == curclass:
        row = [m.group(1), m.group(3).strip()]
        if row not in classes[curclass]['methods']:
            classes[curclass]['methods'].append(row)
        continue
    if cur is None:
        continue
    if line.startswith('@required'):
        required = True
        continue
    if line.startswith('@optional'):
        required = False
        continue
    m = meth.match(line)
    if m and m.group(2) == cur:
        sel = m.group(3).strip()
        row = [m.group(1), sel, required]
        if row not in protocols[cur]['methods']:
            protocols[cur]['methods'].append(row)
json.dump({'protocols': protocols, 'classes': classes}, open(out, 'w'), indent=1, sort_keys=True)
print(len(protocols), 'protocols', sum(len(p['methods']) for p in protocols.values()), 'methods;', len(classes), 'classes', sum(len(c['methods']) for c in classes.values()), 'methods')
