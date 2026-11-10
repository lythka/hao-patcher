"""Generate bound-sampler mip-zero controls for the opt-in prototype."""
import array
import datetime
import hashlib
import itertools
import json
from pathlib import Path
import subprocess

HERE = Path(__file__).resolve().parent
UNIT = HERE.parents[1]


def check(output, revision):
    output = Path(output).resolve()
    output.mkdir(parents=True)
    commands, rows = [], []

    def run(args):
        args = list(map(str, args))
        p = subprocess.run(args, capture_output=True, text=True)
        commands.append(dict(args=args, returncode=p.returncode,
                             stdout=p.stdout, stderr=p.stderr))
        (output / 'commands.json').write_text(json.dumps(commands, indent=2) + '\n')
        p.check_returncode()
        return p.stdout

    probe = UNIT / 'block-reads/probe.m'
    bridge = UNIT.parent / 'shaders/src/replay.m'
    run(['xcrun', 'clang', '-O2', '-fobjc-arc', '-framework', 'Foundation',
         '-framework', 'Metal', probe, '-o', output / 'probe-apple'])
    run(['codesign', '-s', '-', output / 'probe-apple'])
    run(['xcrun', 'clang', '-arch', 'x86_64', '-mmacosx-version-min=13.0', '-O1',
         '-Dmain=unused_replay_main', '-c', bridge, '-o', output / 'bridge.o'])
    run(['xcrun', 'clang', '-arch', 'x86_64', '-mmacosx-version-min=13.0', '-O2',
         '-fobjc-arc', '-framework', 'Foundation', '-framework', 'Metal', probe,
         output / 'bridge.o', '-o', output / 'probe'])
    run(['codesign', '-s', '-', output / 'probe'])
    points = itertools.product(range(-3, 13), range(-3, 11), range(3), range(4))
    (output / 'coords.bin').write_bytes(array.array('i', (v for p in points for v in p)).tobytes())
    for width,height,variant in ((2,1,'block'),(1,2,'block'),(2,1,'sample'),(2,1,'dual')):
        source = (UNIT / 'block-reads/probe.metal').read_text()
        source = source.replace('uint(p.w)', '0u').replace(
            'uint tid [[thread_position_in_grid]]',
            'sampler s [[sampler(0)]], uint tid [[thread_position_in_grid]]')
        source = '\n'.join(s for s in source.splitlines() if 'constexpr sampler' not in s) + '\n'
        for key, value in dict(WIDTH=width, HEIGHT=height, OFFSET_X=-1, OFFSET_Y=2,
                               SECOND_X=width-1, SECOND_Y=height-1).items():
            source = source.replace(key, str(value))
        name = f'dynamic-{width}x{height}' + ('' if variant=='block' else '-'+variant)
        if variant=='sample':
            source=source.replace('out[tid*2+1]=b.value<1,0>();',
                'out[tid*2+1]=tex.sample(s,float2(p.xy)+0.5f,uint(p.z),level(0.0f));')
        if variant=='dual':
            source=source.replace('sampler s [[sampler(0)]],',
                'sampler s [[sampler(0)]], sampler q [[sampler(3)]],')
            source=source.replace('out[tid*2+1]=b.value<1,0>();',
                'auto other=tex.block_read<2,1>(q,p.xy,uint(p.z),int2(-1,2),0u);\n'
                ' out[tid*2+1]=other.value<0,0>();')
        (output / (name + '.metal')).write_text(source)
        run(['xcrun', 'metal', '-std=metal4.1', '-mmacos-version-min=27.0', '-c',
             output / (name + '.metal'), '-o', output / (name + '.air')])
        run(['xcrun', 'metallib', output / (name + '.air'), '-o', output / (name + '.metallib')])
        for normalized in (False, True):
            modes=(0,4) if not normalized else (0,4,2,3)
            for address,address_t in itertools.product(modes,repeat=2):
                for filt in (0,1):
                    for mip in ((0,) if not normalized else (0,1)):
                        case=f'{name}-{int(normalized)}-{address}-{address_t}-{filt}-{mip}'
                        descriptor=dict(normalized=normalized,address=address,addressT=address_t,filter=filt,mip=mip)
                        if variant=='dual':
                            descriptor['secondary']=dict(normalized=not normalized,
                                address=4 if normalized else 2,addressT=0 if normalized else 4,
                                filter=1,mip=0 if normalized else 1)
                        (output/(case+'.json')).write_text(json.dumps(descriptor)+'\n')
                        info=run([output/'probe-apple',output/(name+'.metallib'),output/'coords.bin',
                                  output/(case+'.expected'),output/(case+'.json')])
                        rows.append(dict(case=case,library=name+'.metallib',sampler=descriptor,device=json.loads(info)))
    (output / 'rows.json').write_text(json.dumps(rows, indent=2) + '\n')
    script = ['#!/bin/sh', 'set -eu', 'cd "$(dirname "$0")"',
              'C=$1', 'TRANSLATOR=$2', 'CLIENT=$(dirname "$TRANSLATOR")/gpu-client.dylib', 'mkdir cache']
    for row in rows:
        case = row['case']
        script += ['echo CASE ' + case,
                   'env DYLD_FRAMEWORK_PATH="$C/fw" DYLD_LIBRARY_PATH="$C/dl" '
                   'DYLD_INSERT_LIBRARIES="$C/paths.dylib:$CLIENT:$TRANSLATOR" '
                   'RP_MTLCOMPILER="$C/fw/MTLCompiler.framework/Versions/3802/MTLCompiler" '
                   'RP_CACHE_DIR="$PWD/cache/" RP_ROOT="$C/root" MTL_MONOLITHIC_COMPILER=1 '
                   'COMPAT_CLIENT_ABI=axis-st-v1 COMPAT_BLOCK_READS=per-axis-mip0 RP_SLEEP=0 ./probe ' + row['library'] +
                   ' coords.bin ' + case + '.out ' + case + '.json']
    (output / 'run-native.sh').write_text('\n'.join(script) + '\n')
    inputs = [Path(__file__), probe, UNIT / 'block-reads/probe.metal', bridge,
              bridge.with_name('memo.h'), bridge.with_name('capture-replay.m')]
    report = dict(unit='cache/metallib', revision=revision,
                  commit=run(['git', '-C', UNIT, 'rev-parse', 'HEAD']).strip(),
                  dirty=bool(run(['git','-C',UNIT,'status','--porcelain','--',str(UNIT)]).strip()),
                  date=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                  inputs=[dict(path=str(p), sha256=hashlib.sha256(p.read_bytes()).hexdigest()) for p in inputs],
                  cases=len(rows), intel_gpu_acceptance=False)
    (output / 'build.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


def verify(data, outputs, report):
    rows = json.loads((data / 'rows.json').read_text())
    for row in rows:
        expected = (data / (row['case'] + '.expected')).read_bytes()
        actual = (outputs / (row['case'] + '.out')).read_bytes()
        row.update(equal=actual == expected, scalar_comparisons=len(expected)//4,
                   different_pixels=sum(actual[i:i+16] != expected[i:i+16]
                                        for i in range(0, len(expected), 16)))
    report.write_text(json.dumps(rows, indent=2) + '\n')
    failed = sum(not row['equal'] for row in rows)
    if failed:
        raise ValueError(f'{failed}/{len(rows)} native block controls differ; not a general repair')
    return rows
