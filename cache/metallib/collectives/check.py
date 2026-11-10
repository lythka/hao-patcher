"""Compare actual collective bodies with Apple GPU builtins on sparse lane masks.

Only Intel's lane-ID, active-mask and shuffle leaf intrinsics are replaced by
Metal equivalents. The reduction control flow/arithmetic is the shipped code.
"""
from pathlib import Path
import array
import datetime
import hashlib
import json
import random
import re
import subprocess
import sys
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'lib'))
import collective_runtime


def check(output, revision):
    output = Path(output).resolve()
    output.mkdir(parents=True)
    commands, results = [], []
    def run(args):
        args = list(map(str, args))
        p = subprocess.run(args, capture_output=True, text=True)
        commands.append(dict(args=args, returncode=p.returncode, stdout=p.stdout, stderr=p.stderr))
        (output/'commands.json').write_text(json.dumps(commands, indent=2)+'\n')
        p.check_returncode()
        return p.stdout
    run(['xcrun','clang','-O2','-fobjc-arc','-framework','Metal','-framework','Foundation',HERE/'probe.m','-o',output/'probe'])
    run(['codesign','-s','-',output/'probe'])
    rng=random.Random(81943)
    masks=[0xffffffff,0xaaaaaaaa,0x55555555,0x80000001,1,0x80000000,*range(1,256)]
    masks += [rng.getrandbits(32) or 1 for _ in range(1024)]
    # Integral values ensure floating sums are exact regardless of reduction tree.
    data=array.array('I',[rng.randrange(9) for _ in range(len(masks)*32*4)])
    (output/'input.bin').write_bytes(data.tobytes())
    (output/'masks.bin').write_bytes(array.array('I',masks).tobytes())
    for symbol, body in collective_runtime.sources().items():
        ty=re.search(r'define (.*?) @',body)[1]
        count=int(ty[1]) if ty.startswith('<') else 1
        scalar=ty.split(' x ')[1][:-1] if count>1 else ty
        msl={'float':'float','half':'half','i32':'uint','i16':'ushort','i8':'uchar','i1':'bool','i64':'ulong'}[scalar]
        msl += str(count) if count>1 else ''
        builtin=re.sub(r'_(?:[us]_i\d+|v?\d*f\d+)$','',symbol.removeprefix('air_'))
        value=f'{msl}(input[tid]'+('.'+ 'xyzw'[:count] if count>1 else '.x')+')'
        if symbol.endswith('_s_i32'): msl='int';value='int(input[tid].x)'
        first=symbol=='air_simd_is_first'
        ballot=symbol=='air_simd_ballot_i64'
        if ballot:
            builtin='ulong(simd_ballot'
            value='bool(input[tid].x & 1)'
        argument='' if first else 'bool value, ' if ballot else f'{msl} value, '
        declaration=f'{msl} replacement({argument}ushort lane, ulong mask);'
        encoded='uint4(0)'
        if count==1: encoded='uint4(as_type<uint>(float(result)),0,0,0)'
        else: encoded='uint4('+','.join(f'as_type<uint>(float(result.{c}))' for c in 'xyzw'[:count])+',0'*(4-count)+')'
        if ballot: encoded='uint4(uint(result), uint(result >> 32), 0, 0)'
        template='''#include <metal_stdlib>
using namespace metal;
DECLARATION
kernel void probe(device const uint4 *input [[buffer(0)]], device uint4 *out [[buffer(1)]],
 constant uint *masks [[buffer(2)]], uint tid [[thread_position_in_grid]],
 uint group [[threadgroup_position_in_grid]], ushort lane [[thread_index_in_simdgroup]]) {
 if((masks[group] >> lane)&1) {
  TYPE result=CALL;
  out[tid]=ENCODED;
 }
}
'''.replace('TYPE',msl).replace('ENCODED',encoded)
        outputs=[]
        for variant in ('original','fixed'):
            path=output/(symbol+'-'+variant)
            call=builtin+'('+('' if first else value)+')' if variant=='original' else 'replacement('+('' if first else value+', ')+'lane, ulong(simd_ballot(true)))'
            if ballot and variant=='original': call+=')'
            text=template.replace('DECLARATION',declaration if variant=='fixed' else '').replace('CALL',call)
            path.with_suffix('.metal').write_text(text)
            run(['xcrun','metal','-std=metal3.0','-mmacos-version-min=13.0','-c',path.with_suffix('.metal'),'-o',path.with_suffix('.air')])
            if variant=='fixed':
                text=run(['xcrun','metal-objdump','--disassemble',path.with_suffix('.air')]);text=text[text.index('; ModuleID'):]
                name=re.search(r'@([^ (]*replacement[^ (]*)\(',text)[1]
                text=re.sub(r'^declare .*@'+re.escape(name)+r'\([^\n]*\n','',text,flags=re.M)
                added=re.sub(r'^target triple[^\n]*\n','',body)
                added=added.replace('@'+symbol+'(', '@'+name+'(')
                added=added.replace(') alwaysinline', ', i16 %lane_arg, i64 %mask_arg) alwaysinline') if not first else added.replace('() alwaysinline','(i16 %lane_arg, i64 %mask_arg) alwaysinline')
                intel_body=added
                added=added.replace('call i32 @llvm.igil.active.channel.mask()','trunc i64 %mask_arg to i32')
                added=added.replace('call i32 @llvm.igil.get.simd.id()','zext i16 %lane_arg to i32')
                added=re.sub(r'^declare .*@llvm.igil.(?:active.channel.mask|get.simd.id)[^\n]*\n','',added,flags=re.M)
                added=added.replace('@llvm.igil.simd.shuffle','@compat_shuffle').replace('@llvm.igil.minf','@air.fmin.f32').replace('@llvm.igil.maxf','@air.fmax.f32')
                if '@compat_shuffle' in added:
                    added=re.sub(r'^declare .*@compat_shuffle[^\n]*\n','',added,flags=re.M)
                    added+='''define internal i32 @compat_shuffle(i32 %v, i32 %s) alwaysinline nounwind convergent {
 %short = trunc i32 %s to i16
 %result = call i32 @air.simd_shuffle.u.i32(i32 %v, i16 %short)
 ret i32 %result
}
declare i32 @air.simd_shuffle.u.i32(i32, i16) nounwind readnone convergent
'''
                path.with_suffix('.ll').write_text(text+'\n'+added)
                run(['xcrun','metal-as',path.with_suffix('.ll'),'-o',path.with_suffix('.air')])
                # Standalone native fixture: keep every Intel leaf intrinsic,
                # and omit the Apple-only ballot used by the host comparison.
                native=output/'intel'/symbol
                native.parent.mkdir(exist_ok=True)
                native.with_suffix('.metal').write_text(template.replace('DECLARATION',declaration).replace('CALL',call.replace('ulong(simd_ballot(true))','0')))
                run(['xcrun','metal','-std=metal3.0','-mmacos-version-min=13.0','-c',native.with_suffix('.metal'),'-o',native.with_suffix('.air')])
                nt=run(['xcrun','metal-objdump','--disassemble',native.with_suffix('.air')]);nt=nt[nt.index('; ModuleID'):]
                nt=re.sub(r'^declare .*@'+re.escape(name)+r'\([^\n]*\n','',nt,flags=re.M)
                native.with_suffix('.ll').write_text(nt+'\n'+intel_body)
                run(['xcrun','metal-as',native.with_suffix('.ll'),'-o',native.with_suffix('.air')])
                run(['xcrun','metallib',native.with_suffix('.air'),'-o',native.with_suffix('.metallib')])
            run(['xcrun','metallib',path.with_suffix('.air'),'-o',path.with_suffix('.metallib')])
            device=json.loads(run([output/'probe',path.with_suffix('.metallib'),output/'input.bin',output/'masks.bin',path.with_suffix('.out')]))
            outputs.append(path.with_suffix('.out').read_bytes())
        row=dict(symbol=symbol,**device,equal=outputs[0]==outputs[1],words=len(outputs[0])//4)
        results.append(row);print(json.dumps(row),flush=True)
        (output/'results.json').write_text(json.dumps(results,indent=2)+'\n')
        if not row['equal']:raise ValueError('GPU result differs: '+symbol)
    inputs=[Path(__file__),HERE/'probe.m',Path(collective_runtime.__file__)]
    report=dict(unit='cache/metallib',revision=revision,date=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                commit=run(['git','rev-parse','HEAD']).strip(),inputs=[dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest()) for p in inputs],
                results=results,intel_gpu_acceptance=False)
    (output/'build.json').write_text(json.dumps(report,indent=2)+'\n')
    return report
