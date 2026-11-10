"""Compare quad permutes with Apple builtins and emit actual Intel-IR fixtures."""
from pathlib import Path
import array
import datetime
import hashlib
import json
import random
import re
import subprocess

HERE = Path(__file__).resolve().parent
CASES = []
for components in (1, 2, 3, 4):
    vector = str(components) if components > 1 else ''
    suffix = 'v' + vector + 'f16' if vector else 'f16'
    swizzle = 'xyzw'[:components]
    value = f'as_type<half{vector}>(ushort{vector}(input[tid].{swizzle}))'
    encoded = f'uint4(uint{vector}(as_type<ushort{vector}>(result))' + ',0' * (4-components) + ')'
    CASES.append(('xor-' + suffix, 'half' + vector, 'quad_shuffle_xor', value, encoded))
for components in (1, 3):
    vector = str(components) if components > 1 else ''
    suffix = 'v' + vector + 'f32' if vector else 'f32'
    value = f'as_type<float{vector}>(input[tid].' + 'xyzw'[:components] + ')'
    encoded = f'uint4(as_type<uint{vector}>(result)' + ',0' * (4-components) + ')'
    CASES.append(('broadcast-' + suffix, 'float' + vector, 'quad_broadcast', value, encoded))


def verify(data, masks, output, width, operation, components):
    """Only active destinations reading active sources have defined results."""
    # Native probes may save just dispatched lanes; the host probe saves its
    # entire input-sized allocation (including the unused SIMD16 tail).
    if len(output) not in (len(masks) * width * 4, len(data)):
        raise ValueError('quad readback length differs from the dispatched/allocated buffer')
    checked = different = 0
    for group, mask in enumerate(masks):
        selector = group % 4
        for lane in range(width):
            pos = (group * width + lane) * 4
            if not (mask >> lane & 1):
                expected = [0xcdcdcdcd] * 4
            else:
                source = lane ^ selector if operation == 'quad_shuffle_xor' else lane // 4 * 4 + selector
                if not (mask >> source & 1):
                    continue
                start = (group * width + source) * 4
                expected = list(data[start:start + components])
                if operation == 'quad_shuffle_xor':
                    expected = [v & 65535 for v in expected]
                expected += [0] * (4-components)
                checked += components
            different += sum(a != b for a, b in zip(output[pos:pos + 4], expected))
    different += sum(v != 0xcdcdcdcd for v in output[len(masks) * width * 4:])
    return dict(active_scalar_comparisons=checked, different_words=different)


def verify_outputs(data_dir, outputs, log_path, report):
    data_dir, outputs = Path(data_dir), Path(outputs)
    data, masks = array.array('I'), array.array('I')
    data.frombytes((data_dir / 'input.bin').read_bytes())
    masks.frombytes((data_dir / 'masks.bin').read_bytes())
    log, results = Path(log_path).read_text(), []
    for stem, ty, operation, *_ in CASES:
        segment = log.split(stem + '.metallib\n', 1)[1]
        device = json.loads(re.search(r'\{"device"[^\n]+', segment)[0])
        actual = array.array('I')
        actual.frombytes((outputs / (stem + '.out')).read_bytes())
        row = dict(case=stem, **device,
                   **verify(data, masks, actual, device['width'], operation,
                            int(ty[-1]) if ty[-1].isdigit() else 1))
        results.append(row)
        print(row)
    Path(report).write_text(json.dumps(results, indent=2) + '\n')
    if any(row['different_words'] for row in results):
        raise ValueError('native quad readback differs from bit oracle')
    return results


def check(output, revision):
    output = Path(output).resolve()
    output.mkdir(parents=True)
    commands, results = [], []

    def run(args):
        args = list(map(str, args))
        p = subprocess.run(args, capture_output=True, text=True)
        commands.append(dict(args=args, returncode=p.returncode, stdout=p.stdout, stderr=p.stderr))
        (output / 'commands.json').write_text(json.dumps(commands, indent=2) + '\n')
        p.check_returncode()
        return p.stdout

    run(['xcrun', 'clang', '-O2', '-fobjc-arc', '-framework', 'Metal', '-framework', 'Foundation',
         HERE.parent / 'collectives/probe.m', '-o', output / 'probe'])
    run(['codesign', '-s', '-', output / 'probe'])
    rng = random.Random(90178)
    # Each mask appears at all four selectors; include every nonempty quad mask.
    patterns = [0xffffffff, *[m * 0x11111111 for m in range(1, 16)],
                *[rng.getrandbits(32) for _ in range(1024)]]
    masks = array.array('I', [m for m in patterns for _ in range(4)])
    data = array.array('I', [rng.getrandbits(32) for _ in range(len(masks) * 32 * 4)])
    # Keep signed zero, infinities, signaling/quiet NaNs and subnormals in the corpus.
    special = [0, 0x80000000, 0x7f800000, 0xff800000, 0x7fc00001, 0x7f800001,
               1, 0x80000001, 0x8000, 0x7c00, 0xfc00, 0x7e01, 0x7c01, 0x8001]
    for i in range(32 * 4 * 4):
        data[i] = special[i % len(special)]
    (output / 'input.bin').write_bytes(data.tobytes())
    (output / 'masks.bin').write_bytes(masks.tobytes())
    (output / 'intel').mkdir()
    for stem, ty, operation, value, encoded in CASES:
        body = (HERE / (stem + '.ll')).read_text()
        symbol = re.search(r'^define .*? @(\w+)\(', body, re.M)[1]
        declaration = f'{ty} replacement({ty} value, ushort selector, ushort lane);'
        source = '''#include <metal_stdlib>
using namespace metal;
DECLARATION
kernel void probe(device const uint4 *input [[buffer(0)]], device uint4 *out [[buffer(1)]],
 constant uint *masks [[buffer(2)]], uint tid [[thread_position_in_grid]],
 uint group [[threadgroup_position_in_grid]], ushort lane [[thread_index_in_simdgroup]]) {
 if ((masks[group]>>lane)&1) {
  TYPE result=CALL;
  out[tid]=ENCODED;
 }
}
'''.replace('TYPE', ty).replace('ENCODED', encoded)
        for variant in ('original', 'fixed', 'intel'):
            p = output / ('intel/' + stem if variant == 'intel' else stem + '-' + variant)
            call = (f'{operation}({value},ushort(group%4))' if variant == 'original' else
                    f'replacement({value},ushort(group%4),lane)')
            text = source.replace('DECLARATION', '' if variant == 'original' else declaration).replace('CALL', call)
            p.with_suffix('.metal').write_text(text)
            run(['xcrun', 'metal', '-std=metal3.0', '-mmacos-version-min=13.0', '-c', p.with_suffix('.metal'), '-o', p.with_suffix('.air')])
            if variant != 'original':
                text = run(['xcrun', 'metal-objdump', '--disassemble', p.with_suffix('.air')])
                text = text[text.index('; ModuleID'):]
                name = re.search(r'@([^ (]*replacement[^ (]*)\(', text)[1]
                text = re.sub(r'^declare .*@' + re.escape(name) + r'\([^\n]*\n', '', text, flags=re.M)
                added = re.sub(r'^target triple[^\n]*\n', '', body, flags=re.M)
                added = added.replace('@' + symbol + '(', '@' + name + '(')
                added = added.replace(') alwaysinline', ', i16 %lane_arg) alwaysinline')
                if variant == 'fixed':
                    added = added.replace('call i32 @llvm.igil.get.simd.id()', 'zext i16 %lane_arg to i32')
                    added = re.sub(r'^declare .*@llvm.igil.get.simd.id[^\n]*\n', '', added, flags=re.M)
                    added = added.replace('@llvm.igil.simd.shuffle', '@compat_shuffle')
                    added = re.sub(r'^declare .*@compat_shuffle[^\n]*\n', '', added, flags=re.M)
                    added += '''define internal i32 @compat_shuffle(i32 %v, i32 %s) alwaysinline nounwind convergent {
 %short = trunc i32 %s to i16
 %out = call i32 @air.simd_shuffle.u.i32(i32 %v, i16 %short)
 ret i32 %out
}
declare i32 @air.simd_shuffle.u.i32(i32, i16) nounwind readnone convergent
'''
                p.with_suffix('.ll').write_text(text + '\n' + added)
                run(['xcrun', 'metal-as', p.with_suffix('.ll'), '-o', p.with_suffix('.air')])
            run(['xcrun', 'metallib', p.with_suffix('.air'), '-o', p.with_suffix('.metallib')])
            if variant == 'intel':
                continue
            device = json.loads(run([output / 'probe', p.with_suffix('.metallib'), output / 'input.bin', output / 'masks.bin', p.with_suffix('.out')]))
            actual = array.array('I'); actual.frombytes(p.with_suffix('.out').read_bytes())
            row = dict(symbol=symbol, variant=variant, **device,
                       **verify(data, masks, actual, device['width'], operation,
                                int(ty[-1]) if ty[-1].isdigit() else 1))
            results.append(row)
            (output / 'results.json').write_text(json.dumps(results, indent=2) + '\n')
            print(row, flush=True)
            if row['different_words']:
                raise ValueError('quad readback differs from bit oracle')
    inputs = [Path(__file__), HERE.parent / 'collectives/probe.m',
              *[HERE / (c[0] + '.ll') for c in CASES]]
    report = dict(unit='cache/metallib', revision=revision, operation='quad-check',
                  date=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                  commit=run(['git', 'rev-parse', 'HEAD']).strip(),
                  inputs=[dict(path=str(p), sha256=hashlib.sha256(p.read_bytes()).hexdigest()) for p in inputs],
                  results=results, intel_gpu_acceptance=False)
    (output / 'build.json').write_text(json.dumps(report, indent=2) + '\n')
    return report
