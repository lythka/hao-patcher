#!/usr/bin/python3 -I
"""tccheck.py TRUSTCACHE.im4p MOUNT... : how many trustcache cdhashes are found among the Mach-O slices under each mount."""
import sys, os, subprocess, struct
raw = open(sys.argv[1], 'rb').read()
i = raw.index(b'\x02\x00\x00\x00', 0x10)  # version 2 header after IM4P DER prefix
n, = struct.unpack_from('<I', raw, i + 20)
tc = set(raw[i + 24 + k * 22: i + 24 + k * 22 + 20].hex() for k in range(n))
print('trustcache entries', n, len(tc))
for m in sys.argv[2:]:
    found = set(); slices = 0
    for dp, dn, fn in os.walk(m):
        for f in fn:
            p = os.path.join(dp, f)
            if os.path.islink(p): continue
            try: mg = open(p, 'rb').read(4)
            except OSError: continue
            if mg not in (b'\xcf\xfa\xed\xfe', b'\xca\xfe\xba\xbe', b'\xbe\xba\xfe\xca'): continue
            archs = subprocess.run(['lipo', '-archs', p], capture_output=True, text=True).stdout.split() or ['']
            for a in archs:
                cmd = ['codesign', '-dvvv'] + (['-a', a] if a else []) + [p]
                out = subprocess.run(cmd, capture_output=True, text=True).stderr
                for l in out.splitlines():
                    if l.startswith('CDHash='): slices += 1; found.add(l.split('=', 1)[1].strip()[:40])
    print(m, 'slices', slices, 'in-trustcache', len(found & tc))
