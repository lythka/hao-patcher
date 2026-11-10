"""Compare sift_exec outputs (raw 36-byte MPSImageSIFT keypoint records).

Record layout (from Apple GPU outputs): five float32 (x, y, scale, orientation in degrees,
response), then four 32-bit integers (x, y, octave, layer). Atomic appends order the records
arbitrarily, so records are compared as multisets.

  compare.py pair REF TEST --report OUT.json     every case present in both result folders
  compare.py consistency API KERNEL              kernel replays (per octave) summed vs the API result
"""
import argparse
from collections import Counter
import json
import re
from pathlib import Path
import struct
import sys

STRIDE = 36
FLOATS = ('x', 'y', 'scale', 'orientation', 'response')


def records(path):
    data = path.read_bytes()
    if len(data) % STRIDE:
        raise ValueError(f'{path}: {len(data)} bytes is not a multiple of {STRIDE}')
    return [data[i:i + STRIDE] for i in range(0, len(data), STRIDE)]


def ordered(f):
    """Map a float32 bit pattern to a monotonically ordered integer, for ULP distances."""
    (u,) = struct.unpack('<I', struct.pack('<f', f))
    return u if u < 0x80000000 else -(u & 0x7fffffff)


def ulp(a, b):
    return abs(ordered(a) - ordered(b))


def unpack(r):
    v = struct.unpack('<5f4i', r)
    return v[:5], v[5:]


def angle_error(a, b):
    return abs((a - b + 180) % 360 - 180)


def compare(ref, test):
    """Multiset comparison; for records that are not bit-identical, pair them by their integer
    fields (position, octave, layer) and nearest orientation, and measure float differences."""
    a, b = Counter(ref), Counter(test)
    common = a & b
    only_ref, only_test = list((a - common).elements()), list((b - common).elements())
    row = dict(reference=len(ref), test=len(test), bitwise_equal=sum(common.values()),
               bitwise_identical=(a == b), only_reference=len(only_ref), only_test=len(only_test))
    pending = [unpack(r) for r in only_test]
    worst = {k: dict(abs=0.0, ulp=0) for k in FLOATS}
    paired, unpaired = 0, []
    for r in only_ref:
        fr, ir = unpack(r)
        cands = [i for i, (ft, it) in enumerate(pending) if it == ir]
        if not cands:
            unpaired.append(dict(ints=ir, floats=fr))
            continue
        i = min(cands, key=lambda i: angle_error(pending[i][0][3], fr[3]))
        ft, _ = pending.pop(i)
        paired += 1
        for k, x, y in zip(FLOATS, fr, ft):
            error = angle_error(x, y) if k == 'orientation' else abs(x - y)
            worst[k]['abs'] = max(worst[k]['abs'], error)
            worst[k]['ulp'] = max(worst[k]['ulp'], ulp(x, y))
    # Detection vs orientation: the integer fields (position, octave, layer) identify a detected extremum;
    # each extremum emits one record per orientation peak, and all its records share x/y/scale/response.
    pa, pb = {}, {}
    for r in ref:
        f, i = unpack(r)
        pa.setdefault(i, f)
    for r in test:
        f, i = unpack(r)
        pb.setdefault(i, f)
    both = set(pa) & set(pb)
    rel = [abs(pb[i][4] - pa[i][4]) / abs(pa[i][4]) for i in both if pa[i][4]]
    xy = [max(ulp(pa[i][0], pb[i][0]), ulp(pa[i][1], pb[i][1])) for i in both]
    row.update(positions_reference=len(pa), positions_test=len(pb), positions_common=len(both),
               response_max_relative=max(rel) if rel else 0.0, position_max_ulp=max(xy) if xy else 0)
    row.update(paired_inexact=paired, unpaired_reference=len(unpaired), unpaired_test=len(pending),
               max_error=worst, unpaired_reference_examples=unpaired[:8],
               unpaired_test_examples=[dict(ints=i, floats=f) for f, i in pending[:8]])
    return row


def load(folder):
    rows = {}
    for name in ('api-results.json', 'kernel-results.json'):
        p = folder / name
        if p.exists():
            rows.update({r['case']: r for r in json.loads(p.read_text())})
    return rows


def pair(ref, test, report, suffix=''):
    """Each reference case against the test case of the same name (plus suffix, e.g. -tg8)."""
    rr, tr = load(ref), load(test)
    out = []
    if not tr:
        raise ValueError('no test cases to compare')
    for test_case, t in tr.items():
        case = test_case[:-len(suffix)] if suffix else test_case
        if case not in rr:
            raise ValueError(f'missing reference case: {case}')
        statuses = [x.get('status') for x in t.get('runs', [])]
        row = dict(case=case + suffix, test_error=t.get('error'), test_status=statuses[:1],
                   test_repeat_identical=t.get('repeat_identical'), reference_repeat_identical=rr[case].get('repeat_identical'))
        reference_statuses = [x.get('status') for x in rr[case].get('runs', [])]
        if (t.get('error') or not statuses or any(s != 4 for s in statuses)
                or not reference_statuses or any(s != 4 for s in reference_statuses)):
            row['result'] = 'error'
        else:
            row.update(compare(records(ref / (case + '.keypoints')), records(test / (case + suffix + '.keypoints'))))
            row['result'] = 'bitwise' if row['bitwise_identical'] else (
                'float-only' if row['unpaired_reference'] == 0 and row['unpaired_test'] == 0 else 'different')
        out.append(row)
        print(json.dumps({k: row.get(k) for k in ('case', 'result', 'reference', 'test', 'bitwise_equal', 'unpaired_reference', 'unpaired_test')}), flush=True)
    report.write_text(json.dumps(out, indent=1) + '\n')
    return out


def consistency(api, kernel):
    ar, kr = load(api), load(kernel)
    out = []
    for case, row in ar.items():
        if not case.endswith('-fused') or '-r32float-' not in case:
            continue
        stem = case[len('api-'):].split('-r32float')[0]
        for fn in ('multifetch-apple', 'fused-apple'):
            parts = sorted(k for k in kr if k.startswith(f'kernel-{stem}-o') and k.endswith('-' + fn))
            recs = [r for k in parts for r in records(kernel / (k + '.keypoints'))]
            c = compare(records(api / (case + '.keypoints')), recs)
            out.append(dict(case=case, kernel=fn, octaves=len(parts), identical=c['bitwise_identical'], api=c['reference'], kernel_records=c['test']))
            print(json.dumps(out[-1]), flush=True)
    return out


SLOTS = ('block0', 'block1', 'read0', 'read1', 'sample0', 'sample1')


def block(ref, test, cases, report=None):
    """block_probe outputs: per slot, scalars and texels that differ between devices, split by whether
    the texel is inside the texture, by row offset and by layer; plus, per device, block vs read() of
    the same texel (inside) and block vs zero (outside)."""
    out = []
    meta = json.loads(cases.read_text())
    for row in json.loads((ref / 'block-results.json').read_text()):
        case = row['case']
        coords = (ref / (case + '.coords')).read_bytes()
        a, b = (ref / (case + '.block')).read_bytes(), (test / (case + '.block')).read_bytes() if test else None
        w, h = next((m['width'], m['height']) for m in meta if m['name'] == case)
        r = dict(case=case, coordinates=len(coords) // 16, scalars=len(a) // 4,
                 differ={s: 0 for s in SLOTS}, differ_inside={s: 0 for s in SLOTS}, differ_by_offset={}, max_abs={s: 0.0 for s in SLOTS},
                 self_block_vs_read={'reference': 0, 'test': 0}, self_outside_nonzero={'reference': 0, 'test': 0})
        for i in range(len(coords) // 16):
            x, y, layer, o = struct.unpack_from('<4i', coords, i * 16)
            for k, s in enumerate(SLOTS):
                off = (i * 6 + k) * 16
                va = struct.unpack_from('<4f', a, off)
                qx = x + (k % 2)
                inside = 0 <= qx < w and 0 <= y + o < h
                for dev, data in (('reference', a), ('test', b)):
                    if data is None or k > 1:
                        continue
                    v, rd = data[off:off + 16], data[(i * 6 + 2 + k) * 16:(i * 6 + 3 + k) * 16]
                    if inside and v != rd:
                        r['self_block_vs_read'][dev] += 1
                    if not inside and struct.unpack('<4f', v)[:3] != (0, 0, 0):
                        r['self_outside_nonzero'][dev] += 1
                if b is None or a[off:off + 16] == b[off:off + 16]:
                    continue
                vb = struct.unpack_from('<4f', b, off)
                r['differ'][s] += 1
                r['differ_inside'][s] += inside
                key = f'{s}/o{o}'
                r['differ_by_offset'][key] = r['differ_by_offset'].get(key, 0) + 1
                r['max_abs'][s] = max(r['max_abs'][s], max(abs(p - q) for p, q in zip(va, vb)))
        out.append(r)
        print(json.dumps({k: r[k] for k in ('case', 'coordinates', 'differ', 'self_block_vs_read')}), flush=True)
    if report:
        report.write_text(json.dumps(out, indent=1) + '\n')
    return out


def median(v):
    v = sorted(v)
    return (v[len(v) // 2] + v[(len(v) - 1) // 2]) / 2 if v else None


def timing(rows):
    """GPU seconds per dispatch (command buffer) in a result folder: first dispatch of each case and the
    median of the rest; pipeline creation seconds for the cases that created one."""
    first = [r['runs'][0]['gpu_s'] for r in rows if r.get('runs') and r['runs'][0]['status'] == 4]
    rest = [x['gpu_s'] for r in rows for x in r.get('runs', [])[1:] if x['status'] == 4]
    pipe = [r['pipeline_s'] for r in rows if r.get('pipeline_cold') and 'pipeline_s' in r]
    return dict(dispatches=len(first) + len(rest), first_median_s=median(first), repeat_median_s=median(rest),
                repeat_min_s=min(rest) if rest else None, repeat_max_s=max(rest) if rest else None,
                pipeline_s=pipe, total_gpu_s=sum(first) + sum(rest))


def group(case):
    """kernel-WxH-oN-KERNEL-SET[-tgT] -> KERNEL-SET[-tgT]; api-WxH-FORMAT-MODE -> api-MODE."""
    m = re.search(r'-o\d+-(.*)$', case)
    return m[1] if case.startswith('kernel-') and m else 'api-' + case.split('-')[-1]


def summary(pairs, ref, test_cold, test_warm):
    """Aggregate pair reports by kernel and constant set, plus timings per group (cold/warm processes)."""
    groups = {}
    for row in json.loads(pairs.read_text()):
        key = group(row['case'])
        g = groups.setdefault(key, dict(cases=0, bitwise=0, float_only=0, different=0, error=0, reference_records=0,
                                        test_records=0, bitwise_records=0, positions_reference=0, positions_test=0,
                                        positions_common=0, response_max_relative=0.0, position_max_ulp=0,
                                        nondeterministic_test=0, max_error={k: dict(abs=0.0, ulp=0) for k in FLOATS}, failing=[]))
        g['cases'] += 1
        g[{'bitwise': 'bitwise', 'float-only': 'float_only', 'different': 'different', 'error': 'error'}[row['result']]] += 1
        g['reference_records'] += row.get('reference', 0)
        g['test_records'] += row.get('test', 0)
        g['bitwise_records'] += row.get('bitwise_equal', 0)
        for k in ('positions_reference', 'positions_test', 'positions_common'):
            g[k] += row.get(k, 0)
        for k in ('response_max_relative', 'position_max_ulp'):
            g[k] = max(g[k], row.get(k, 0))
        g['nondeterministic_test'] += row.get('test_repeat_identical') is False
        for k in FLOATS:
            for m in ('abs', 'ulp'):
                g['max_error'][k][m] = max(g['max_error'][k][m], row.get('max_error', {}).get(k, {}).get(m, 0))
        if row['result'] != 'bitwise':
            g['failing'].append(row['case'])
    for name, folder in (('reference', ref), ('test_cold', test_cold), ('test_warm', test_warm)):
        rows = load(folder) if folder else {}
        for key, g in groups.items():
            sel = [r for c, r in rows.items() if group(c) == key]
            g['timing_' + name] = timing(sel)
    return groups


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest='command', required=True)
    b = sub.add_parser('block')
    b.add_argument('reference', type=Path)
    b.add_argument('test', type=Path, nargs='?')
    b.add_argument('--cases', type=Path, required=True, help='block-cases.json (texture sizes)')
    b.add_argument('--report', type=Path)
    m = sub.add_parser('summary')
    m.add_argument('pairs', type=Path)
    m.add_argument('reference', type=Path)
    m.add_argument('cold', type=Path)
    m.add_argument('warm', type=Path)
    m.add_argument('--report', type=Path, required=True)
    s = sub.add_parser('pair')
    s.add_argument('reference', type=Path)
    s.add_argument('test', type=Path)
    s.add_argument('--report', type=Path, required=True)
    s.add_argument('--suffix', default='', help='test case names are the reference names plus this')
    c = sub.add_parser('consistency')
    c.add_argument('api', type=Path)
    c.add_argument('kernel', type=Path)
    a = p.parse_args()
    if a.command == 'pair':
        rows = pair(a.reference, a.test, a.report, a.suffix)
        if any(r['result'] != 'bitwise' or r.get('test_repeat_identical') is False for r in rows):
            sys.exit(1)
    elif a.command == 'block':
        block(a.reference, a.test, a.cases, a.report)
    elif a.command == 'summary':
        a.report.write_text(json.dumps(summary(a.pairs, a.reference, a.cold, a.warm), indent=1) + '\n')
    else:
        rows = consistency(a.api, a.kernel)
        if not all(r['identical'] for r in rows):
            sys.exit(1)


if __name__ == '__main__':
    main()
