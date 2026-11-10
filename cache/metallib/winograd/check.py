"""Apple-GPU numerical controls for the Winograd lowering; no laptop access."""
from pathlib import Path
import array
import datetime
import hashlib
import json
import itertools
import math
import random
import re
import subprocess
import sys
HERE=Path(__file__).resolve().parent
UNIT=HERE.parent
sys.path.insert(0,str(UNIT/'lib'))
import winograd_matrix
import winograd_offsets

def matrix_checks(w, command):
    results = []
    source = (HERE / 'matrix.metal').read_text()
    for size in (4, 8):
        for ty, memty in [('float', 'float'), ('half', 'half'), ('float', 'half')]:
            label = f'shared-{size}-{ty}-{memty}'
            p = w / label
            s = source.replace('MEMTYPE', memty).replace('TYPE', ty)
            p.with_suffix('.metal').write_text(s)
            command(['xcrun', 'metal', '-std=metal3.0', '-mmacos-version-min=13.0', '-c', str(p.with_suffix('.metal')), '-o', str(p.with_suffix('.air'))], check=True)
            s = command(['xcrun', 'metal-objdump', '--disassemble', str(p.with_suffix('.air'))], text=True)
            s = s[s.index('; ModuleID'):]
            name = re.search('@([^ (]*matrix_op[^ (]*)\\(', s)[1]
            bits = 32 if ty == 'float' else 16
            s = s.replace('@' + name + '(', f'@llvm.agx2.f{bits}matmad{size}x{size}.v2f{bits}(')
            p.with_suffix('.ll').write_text(s)
            fixed, count = winograd_matrix.rewrite(s)
            outputs = []
            for tag, text in [('original', s), ('fixed', fixed)]:
                q = w / (label + '-' + tag)
                q.with_suffix('.ll').write_text(text)
                command(['xcrun', 'metal-as', str(q.with_suffix('.ll')), '-o', str(q.with_suffix('.air'))], check=True)
                command(['xcrun', 'metallib', str(q.with_suffix('.air')), '-o', str(q.with_suffix('.metallib'))], check=True)
                out = []
                for mode, groups in [('integer', 1024), ('random', 1024), ('basis', 4096)]:
                    dest = w / (label + '-' + tag + '-' + mode + '.out')
                    inputs = [str(w / (v + '.bin' if mode == 'basis' else mode + '-' + v + '.bin')) for v in 'abc']
                    command([str(w / 'probe-shared'), str(q.with_suffix('.metallib')), *inputs, str(dest), str(groups)], check=True)
                    out.append(dest.read_bytes())
                outputs.append(out)
            for i, mode in enumerate(['integer', 'random', 'basis']):
                a, b = (outputs[0][i], outputs[1][i])
                row = dict(size=size, type=ty, memory_type=memty, mode=mode, equal=a == b, different=sum((a[j:j + 4] != b[j:j + 4] for j in range(0, len(a), 4))), outputs=len(a) // 4)
                results.append(row)
                print(row, flush=True)
    return results

def sample_checks(w, command):
    rng = random.Random(98143)
    data = array.array('f')
    edge = [-1000000, -1.001, -0.5, 0, 0.1, 0.49999, 0.5, 0.50001, 1, 6.9999, 7, 8.9999, 9, 1000000]
    for i in range(4096):
        data.extend([edge[i % len(edge)] if i < 256 else rng.uniform(-16, 32), edge[i // len(edge) % len(edge)] if i < 256 else rng.uniform(-16, 32)])
    (w / 'coordinates.bin').write_bytes(data.tobytes())
    base = (HERE / 'sample.metal').read_text()
    results = []
    for ty, table in itertools.product(('float', 'half'), (False, True)):
        for arraytex in (False, True):
            for norm in (False, True):
                label = f'sample-{ty}-{arraytex}-{norm}' + ('-table' if table else '')
                p = w / label
                s = base.replace('texture2d<float>', ('texture2d_array' if arraytex else 'texture2d') + '<' + ty + '>').replace('out[tid]=image.sample(s,coords[tid],offset)', 'out[tid]=float4(image.sample(s,coords[tid],' + ('tid&1,' if arraytex else '') + 'offset))')
                if table:
                    s = s.replace('int2 offset=int2((tid&2),((tid>>1)&2));',
                                  'int2 offsets[9]={int2(0,0),int2(-1,0),int2(1,0),'
                                  'int2(0,-1),int2(0,1),int2(-1,-1),int2(1,-1),'
                                  'int2(-1,1),int2(1,1)}; int2 offset=offsets[tid%9];')
                if norm:
                    s = s.replace('coord::pixel', 'coord::normalized').replace('address::clamp_to_edge', 'address::repeat')
                p.with_suffix('.metal').write_text(s)
                command(['xcrun', 'metal', '-std=metal3.0', '-mmacos-version-min=13.0', '-c', str(p.with_suffix('.metal')), '-o', str(p.with_suffix('.air'))], check=True)
                s = command(['xcrun', 'metal-objdump', '--disassemble', str(p.with_suffix('.air'))], text=True)
                s = s[s.index('; ModuleID'):]
                fixed, report = winograd_offsets.rewrite(s, include_private_tables=table)
                assert report['calls'] == 1
                assert report['extra_samples'] == (8 if table else 3)
                outputs = []
                for tag, text in [('original', s), ('fixed', fixed)]:
                    q = w / (label + '-' + tag)
                    q.with_suffix('.ll').write_text(text)
                    command(['xcrun', 'metal-as', str(q.with_suffix('.ll')), '-o', str(q.with_suffix('.air'))], check=True)
                    command(['xcrun', 'metallib', str(q.with_suffix('.air')), '-o', str(q.with_suffix('.metallib'))], check=True)
                    command([str(w / 'sample-probe'), str(q.with_suffix('.metallib')), str(w / 'coordinates.bin'), str(q.with_suffix('.out')), str(int(arraytex))], check=True)
                    outputs.append(q.with_suffix('.out').read_bytes())
                row = dict(type=ty, array=arraytex, normalized=norm, private_table=table,
                           outputs=len(outputs[0]) // 4, equal=outputs[0] == outputs[1])
                print(row, flush=True)
                results.append(row)
    return results


def check(output, revision):
    output = Path(output).resolve()
    output.mkdir(parents=True)
    commands = []

    def command(args, **unused):
        args = list(map(str, args))
        result = subprocess.run(args, capture_output=True, text=True)
        commands.append(dict(args=args, returncode=result.returncode,
                             stdout=result.stdout, stderr=result.stderr))
        (output / 'commands.json').write_text(json.dumps(commands, indent=2) + '\n')
        result.check_returncode()
        return result.stdout

    for source, binary in [('probe.m', 'probe-shared'), ('sample-probe.m', 'sample-probe')]:
        command(['xcrun', 'clang', '-O2', '-fobjc-arc', '-framework', 'Foundation',
                 '-framework', 'Metal', HERE / source, '-o', output / binary])
        command(['codesign', '-s', '-', output / binary])
    n, groups = 64, 4096
    a, b, c = [array.array('f', [0]) * (groups * n) for _ in range(3)]
    for i in range(n):
        for j in range(n):
            a[(i * n + j) * n + i] = 1
            b[(i * n + j) * n + j] = 1
    for label, data in [('a', a), ('b', b), ('c', c)]:
        (output / (label + '.bin')).write_bytes(data.tobytes())
    rng = random.Random(98143)
    for mode in ['integer', 'random']:
        for label in 'abc':
            data = array.array('f', (rng.randint(-8, 8) if mode == 'integer' else
                                    math.ldexp(rng.uniform(-1, 1), rng.randint(-8, 8))
                                    for _ in range(1024 * 64)))
            (output / (mode + '-' + label + '.bin')).write_bytes(data.tobytes())
    matrices = matrix_checks(output, command)
    samples = sample_checks(output, command)
    sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
    sources = [*sorted(HERE.glob('*.*')), Path(winograd_matrix.__file__), Path(winograd_offsets.__file__)]
    git = lambda *args: command(['git', '-C', UNIT, *args]).strip()
    report = dict(schema=1, unit='cache/metallib', revision=revision,
                  commit=git('rev-parse', 'HEAD'), dirty=bool(git('status', '--porcelain', '--', UNIT)),
                  date=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                  operation='winograd-check',
                  inputs=[dict(path=str(p), sha256=sha(p)) for p in sources],
                  toolchain=command(['xcrun', 'metal', '--version']).strip(),
                  devices=sorted(set(re.findall(r'device=([^\n]+)', '\n'.join(c['stderr'] for c in commands)))),
                  matrix_cases=matrices, sample_cases=samples,
                  scalar_comparisons=sum(row['outputs'] for row in matrices + samples),
                  intel_gpu_acceptance=False)
    (output / 'build.json').write_text(json.dumps(report, indent=2) + '\n')
    if not all(row['equal'] for row in matrices + samples):
        raise ValueError('GPU output mismatch: ' + str(output / 'build.json'))
    return report
