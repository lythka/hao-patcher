"""Deterministic inputs for sift_exec.

  gen.py sources OUT [--sizes ...] [--formats ...]
      Source images (integer arithmetic only, so the bytes are the same on any host) and api-cases.json.
  gen.py kernel-cases OUT REF_API_OUT
      From a reference API run with pyramid dumps (the Apple GPU's MPS pyramids): copy each octave's
      DoG/Gaussian textures into OUT and write kernel-cases.json, one direct dispatch per octave of
      both fused extraction kernels, on identical inputs for every device.
"""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import shutil
import struct

R8UNORM, R16UNORM, R16SNORM, R16FLOAT, R32FLOAT = 10, 20, 22, 25, 55
BYTES = {R8UNORM: 1, R16UNORM: 2, R16FLOAT: 2, R32FLOAT: 4}
# MPSImageSIFT accepts single-channel sources only (BGRA8Unorm fails its assertion).
NAMES = {R8UNORM: 'r8unorm', R16UNORM: 'r16unorm', R16FLOAT: 'r16float', R32FLOAT: 'r32float'}
MULTIFETCH = 'ExtractFeaturePointsAndOrientationFusedMultifetch'
FUSED = 'ExtractFeaturePointsAndOrientationFused'
# Function constants (124 kMPSUserConstant, 127 kMPSDeviceCaps; both required uints). Every device runs
# every set, so each comparison is the same specialization on the same inputs.
#   apple: what MPS passes on an Apple silicon GPU for the multifetch dispatch (captured)
#   intel: the HD 4000's device caps (137, captured from MPS there) with the user constant 3 that MPS
#          passes on both devices for ExtractFeaturePoints (MPS on the HD 4000 never picks multifetch)
#   mps0/mps1: the zero-layer and one-layer specializations used by the block-read translation controls
CONSTANTS = {'apple': '124.UInt=3;127.UInt=26316799', 'intel': '124.UInt=3;127.UInt=137',
             'mps0': '124.UInt=0;127.UInt=0', 'mps1': '124.UInt=1;127.UInt=1'}
SETS = {MULTIFETCH: ('apple', 'intel', 'mps0', 'mps1'), FUSED: ('apple', 'intel')}


def hash32(x, y, seed):
    h = (x * 73856093 ^ y * 19349663 ^ seed * 83492791) & 0xffffffff
    h ^= h >> 13
    h = (h * 0x5bd1e995) & 0xffffffff
    h ^= h >> 15
    return h


def intensity(width, height, seed):
    """Image values as integers in 0..65535: soft bright and dark disks (radius 1..6) on mid-grey
    plus +-256 noise. Gives tens to hundreds of keypoints, below maximumKeyPoints (5000)."""
    field = [[32768] * width for _ in range(height)]
    for i in range(max(6, width * height // 200)):
        h = hash32(i, 7, seed)
        cx, cy, r, sign = h % width, (h >> 12) % height, 1 + (h >> 24) % 6, 1 if h & 1 else -1
        for y in range(max(0, cy - 2 * r), min(height, cy + 2 * r + 1)):
            for x in range(max(0, cx - 2 * r), min(width, cx + 2 * r + 1)):
                k = 4 * r * r - (x - cx) ** 2 - (y - cy) ** 2
                if k >= 0:
                    field[y][x] += sign * 30000 * k * k // (16 * r ** 4)
    for y in range(height):
        for x in range(width):
            field[y][x] = min(65535, max(0, field[y][x] + (hash32(x, y, seed) & 511) - 256))
    return field


def encode(field, fmt):
    out = bytearray()
    for row in field:
        for v in row:
            if fmt == R32FLOAT:
                out += struct.pack('<f', v / 65535)
            elif fmt == R16FLOAT:
                out += struct.pack('<e', v / 65535)
            elif fmt == R8UNORM:
                out.append((v * 255 + 32767) // 65535)
            elif fmt == R16UNORM:
                out += struct.pack('<H', v)
    return bytes(out)


def sources(out, sizes, formats, dump):
    out.mkdir(parents=True)
    by_name = {v: k for k, v in NAMES.items()}
    cases, hashes = [], {}
    for size in sizes:
        w, h = map(int, size.split('x'))
        field = intensity(w, h, w * 1000 + h)
        for fname in formats:
            fmt = by_name[fname]
            src = f'src-{w}x{h}-{fname}.bin'
            data = encode(field, fmt)
            (out / src).write_bytes(data)
            hashes[src] = hashlib.sha256(data).hexdigest()
            for fused in (1, 0):
                cases.append(dict(name=f'api-{w}x{h}-{fname}-{"fused" if fused else "unfused"}', width=w, height=h,
                                  format=fmt, bytesPerPixel=BYTES[fmt], source=src, fused=fused, repeats=5,
                                  dumpPyramids=bool(dump and fused)))
    (out / 'api-cases.json').write_text(json.dumps(cases, indent=1) + '\n')
    # The HD 4000 runs only fused cases: MPS's unfused ExtractFeaturePoints dispatch stalls its GPU for
    # about 6 s and ends in "Internal Error (00000813)".
    (out / 'api-fused-cases.json').write_text(json.dumps([c for c in cases if c['fused']], indent=1) + '\n')
    (out / 'inputs.json').write_text(json.dumps(hashes, indent=1, sort_keys=True) + '\n')


def kernel_cases(out, ref, formats=('r32float',)):
    cases = json.loads((out / 'api-cases.json').read_text())
    by_case = {c['name']: c for c in cases}
    hashes = json.loads((out / 'inputs.json').read_text())
    rows = []
    for result in json.loads((ref / 'api-results.json').read_text()):
        api = by_case[result['case']]
        if not api['fused'] or NAMES[api['format']] not in formats:
            continue
        octave = 0
        for d in result['dispatches']:
            if not d['function'].startswith(FUSED):
                continue
            files = {}
            for slot in ('texture0', 'texture1'):
                name = d[slot + '_dump']
                shutil.copyfile(ref / name, out / name)
                hashes[name] = hashlib.sha256((out / name).read_bytes()).hexdigest()
                files[slot] = name
            t0, t1 = d['texture0'], d['texture1']
            capture = {k: v for k, v in api.items() if k not in ('dumpPyramids', 'repeats', 'name')}
            for fn, cset in ((fn, c) for fn in (MULTIFETCH, FUSED) for c in SETS[fn]):
                rows.append(dict(name=f'kernel-{api["width"]}x{api["height"]}-o{octave}-{"multifetch" if fn == MULTIFETCH else "fused"}-{cset}',
                                 function=fn, constants=CONSTANTS[cset], captureFunction=FUSED, octave=octave, capture=capture,
                                 width=t0['width'], height=t0['height'],
                                 dog=files['texture0'], dogFormat=t0['format'], dogLayers=t0['array'],
                                 gauss=files['texture1'], gaussFormat=t1['format'], gaussLayers=t1['array'],
                                 params=d['bytes0'], fallback=d, recordedDispatch=d, repeats=20,
                                 reference_constants=d['function'].split('|', 1)[1]))
            octave += 1
    (out / 'kernel-cases.json').write_text(json.dumps(rows, indent=1) + '\n')
    (out / 'inputs.json').write_text(json.dumps(hashes, indent=1, sort_keys=True) + '\n')
    diagnostic_cases(out)
    block_cases(out, rows)


def block_cases(out, rows, names=('kernel-16x16-o0', 'kernel-97x61-o0', 'kernel-255x129-o1', 'kernel-1000x700-o3')):
    """block_probe inputs: the DoG texture and sampler of a few kernel cases (odd sizes, several octaves)."""
    cases = []
    for row in rows:
        stem = row['name'].rsplit('-', 2)[0]
        if stem in names and row['name'].endswith('-multifetch-apple'):
            cases.append(dict(name='block-' + stem[len('kernel-'):], texture=row['dog'], format=row['dogFormat'],
                              width=row['width'], height=row['height'], layers=row['dogLayers'],
                              sampler=row['fallback']['sampler0']))
    (out / 'block-cases.json').write_text(json.dumps(cases, indent=1) + '\n')


def diagnostic_cases(out):
    """Baseline controls plus width8 dispatches for diagnose_width.py's library only."""
    baseline, width8 = [], []
    for row in json.loads((out / 'kernel-cases.json').read_text()):
        if not row['name'].endswith(('-multifetch-mps0', '-multifetch-mps1')):
            continue
        row = dict(row, recordedDispatch=row['fallback'])
        baseline.append(row)
        if row['name'].endswith('mps1'):
            row = copy.deepcopy(row)
            dispatch = row['recordedDispatch']
            border = struct.unpack_from('<H', bytes.fromhex(dispatch['bytes0']), 24)[0]
            dispatch['threads'] = [8, 1, 1]
            dispatch['grid'][0] = (row['width'] - 2 * border + 13) // 14
            width8.append(row)
    (out / 'baseline-cases.json').write_text(json.dumps(baseline, indent=1) + '\n')
    (out / 'width8-cases.json').write_text(json.dumps(width8, indent=1) + '\n')


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest='command', required=True)
    s = sub.add_parser('sources')
    s.add_argument('output', type=Path)
    s.add_argument('--sizes', default='16x16,17x23,31x33,64x64,97x61,128x128,255x129,256x256,321x200,640x480,1000x700')
    s.add_argument('--formats', default='r32float,r16float,r16unorm,r8unorm')
    s.add_argument('--dump-pyramids', action='store_true')
    k = sub.add_parser('kernel-cases')
    k.add_argument('output', type=Path)
    k.add_argument('reference', type=Path)
    a = p.parse_args()
    if a.command == 'sources':
        sources(a.output, a.sizes.split(','), a.formats.split(','), a.dump_pyramids)
    else:
        kernel_cases(a.output, a.reference)


if __name__ == '__main__':
    main()
