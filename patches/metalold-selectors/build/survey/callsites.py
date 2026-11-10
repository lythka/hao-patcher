"""callsites.py SCRATCH MISSING_SELECTORS_JSON OUT_TSV
For every image disassembled in SCRATCH/asm, find the code sites that load a selector-reference slot of a missing
selector (rip-relative operand), the function they sit in (LC_FUNCTION_STARTS of SCRATCH/extract/<image>) and whether
that function also loads respondsToSelector:/instancesRespondToSelector:/conformsToProtocol: (guarded) or
supportsFamily:/supportsFeatureSet: (family-gated)."""
import bisect
import json
import os
import re
import struct
import subprocess
import sys

GUARDS = {'respondsToSelector:', 'instancesRespondToSelector:', 'conformsToProtocol:'}
GATES = {'supportsFamily:', 'supportsFeatureSet:'}
RIP = re.compile(r'(-?0x[0-9a-f]+)\(%rip\)')
LINE = re.compile(r'^([0-9a-f]{16})\t(\S+)\t?(.*)$')


def function_starts(binary):
    text = subprocess.run(['otool', '-l', binary], capture_output=True, text=True).stdout
    m = re.search(r'LC_FUNCTION_STARTS\n\s+cmdsize \d+\n\s+dataoff (\d+)\n\s+datasize (\d+)', text)
    seg = re.search(r'segname __TEXT\n\s+vmaddr 0x([0-9a-f]+)', text)
    if not m or not seg:
        return []
    off, size = int(m.group(1)), int(m.group(2))
    base = int(seg.group(1), 16)
    with open(binary, 'rb') as f:
        f.seek(off)
        data = f.read(size)
    starts, addr, i = [], base, 0
    while i < len(data):
        value, shift = 0, 0
        while True:
            byte = data[i]
            i += 1
            value |= (byte & 0x7f) << shift
            shift += 7
            if not byte & 0x80:
                break
        if value == 0 and starts:
            break
        addr += value
        starts.append(addr)
    return starts


def main(scratch, missing_json, out):
    missing = set(json.load(open(missing_json)))
    rows = []
    for name in sorted(os.listdir(f'{scratch}/asm')):
        if not name.endswith('.asm'):
            continue
        image = name[:-4]
        slots = {}
        for line in open(f'{scratch}/selrefs-addr/{image}.txt'):
            m = re.match(r'^0x([0-9a-f]+) => 0x[0-9a-f]+: (.*)$', line.rstrip('\n'))
            if m:
                slots[int(m.group(1), 16)] = m.group(2)
        binary = f'{scratch}/extract/{image}'
        if not os.path.exists(binary):
            found = [os.path.join(d, image) for d, _, fs in os.walk(f'{scratch}/extract') if image in fs]
            binary = found[0] if found else None
        starts = function_starts(binary) if binary else []
        loads = []        # (addr, selector)
        labels = []       # (addr, symbol)
        prev = None
        label = None
        for line in open(f'{scratch}/asm/{name}', errors='replace'):
            line = line.rstrip('\n')
            m = LINE.match(line)
            if not m:
                if line.endswith(':') and not line.startswith(' '):
                    label = line[:-1]
                continue
            addr = int(m.group(1), 16)
            if label is not None:
                labels.append((addr, label))
                label = None
            if prev is not None:
                paddr, ptext = prev
                r = RIP.search(ptext)
                if r:
                    target = addr + int(r.group(1), 16)
                    sel = slots.get(target)
                    if sel:
                        loads.append((paddr, sel))
            prev = (addr, m.group(3))
        by_function = {}
        for addr, sel in loads:
            i = bisect.bisect_right(starts, addr) - 1
            fn = starts[i] if i >= 0 else addr
            by_function.setdefault(fn, []).append((addr, sel))
        label_addrs = [a for a, _ in labels]
        for fn, items in by_function.items():
            sels = {s for _, s in items}
            hits = [(a, s) for a, s in items if s in missing]
            if not hits:
                continue
            li = bisect.bisect_right(label_addrs, fn) - 1
            symbol = labels[li][1] if li >= 0 else ''
            guarded = bool(sels & GUARDS)
            gated = bool(sels & GATES)
            for addr, sel in hits:
                rows.append((image, sel, f'{addr:#x}', f'{fn:#x}', 'guarded' if guarded else ('gated' if gated else 'unguarded'), symbol[:120]))
    with open(out, 'w') as f:
        f.write('image\tselector\tsite\tfunction\tguard\tsymbol\n')
        for r in sorted(rows):
            f.write('\t'.join(r) + '\n')
    print(len(rows), 'call sites in', len({r[0] for r in rows}), 'images')


if __name__ == '__main__':
    main(*sys.argv[1:4])
