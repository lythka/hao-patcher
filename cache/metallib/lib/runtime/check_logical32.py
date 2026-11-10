"""Generated IR controls for the reviewed logical32 compiler pass, without Apple IR."""
import json
from pathlib import Path
import subprocess
from .replay_runtime import paths

FUNCTION='ExtractFeaturePointsAndOrientationFusedMultifetch'
PARAMS='i8* %a, i8* %b, i8* %params, float* %out, i32* %count, float addrspace(3)* %hist, i8* %sam, <3 x i16> %group, <3 x i16> %size, <3 x i16> %thread'
TYPES='i8*, i8*, i8*, float*, i32*, float addrspace(3)*, i8*, <3 x i16>, <3 x i16>, <3 x i16>'
NEUTRAL='0xC7EFFFFFE0000000'


def fixture(full=False):
    out=[f'define void @{FUNCTION}({PARAMS}) {{', 'entry:',
         ' %lane = extractelement <3 x i16> %thread, i32 0',
         ' %vary = icmp ult i16 %lane, 16', ' %neighbor = icmp ult i16 %lane, 31']
    for i in range(6 if full else 4):
        out.append(f' %s{i} = call <4 x float> @air.simd_shuffle_down.v4f32(<4 x float> zeroinitializer, i16 1)')
    for i in range(4 if full else 0):
        out += [f' br label %pre{i}', f'pre{i}:',
                f' %ballot{i} = call i64 @air.simd_ballot.i64(i1 %vary)',
                f' %active{i} = icmp ne i64 %ballot{i}, 0',
                f' br i1 %active{i}, label %work{i}, label %done{i}', f'work{i}:']
        for j in range(4):
            out += [f' %f{i}_{j} = call float @air.simd_broadcast.f32(float 1.0, i16 0)',
                    f' %u{i}_{j} = call i32 @air.simd_broadcast.u.i32(i32 1, i16 0)']
        out += [' call void @air.simdgroup.barrier(i32 2, i32 4)',
                f' %start{i} = icmp ult i16 %lane, 36',
                f' br i1 %start{i}, label %loop{i}, label %exit{i}', f'loop{i}:',
                f' %local{i} = phi float [{NEUTRAL}, %work{i}], [%fold{i}, %loop{i}]',
                f' %index{i} = phi i16 [%lane, %work{i}], [%next{i}, %loop{i}]',
                f' %p{i} = getelementptr float, float addrspace(3)* %hist, i16 %index{i}',
                f' %v{i} = load float, float addrspace(3)* %p{i}',
                f' %max{i} = call float @air.simd_max.f32(float %v{i})',
                f' %fold{i} = call float @air.fast_fmax.f32(float %local{i}, float %max{i})',
                f' %next{i} = add i16 %index{i}, 32',
                f' %more{i} = icmp ult i16 %next{i}, 36',
                f' br i1 %more{i}, label %loop{i}, label %exit{i}', f'exit{i}:',
                f' %exitmax{i} = phi float [{NEUTRAL}, %work{i}], [%fold{i}, %loop{i}]',
                f' %result{i} = call float @air.simd_broadcast.f32(float %exitmax{i}, i16 0)',
                f' store float %result{i}, float* %out']
        out += [' call void @air.simdgroup.barrier(i32 2, i32 4)']*3
        out += [f' br label %done{i}', f'done{i}:']
    out += [' ret void', '}',
            'declare <4 x float> @air.simd_shuffle_down.v4f32(<4 x float>, i16) convergent readnone',
            'declare float @air.simd_broadcast.f32(float, i16) convergent readnone',
            'declare i32 @air.simd_broadcast.u.i32(i32, i16) convergent readnone',
            'declare i64 @air.simd_ballot.i64(i1) convergent readnone',
            'declare void @air.simdgroup.barrier(i32, i32) convergent',
            'declare float @air.simd_max.f32(float) convergent readnone',
            'declare float @air.fast_fmax.f32(float, float) readnone',
            '!air.kernel = !{!0}', f'!0 = !{{void ({TYPES})* @{FUNCTION}, !1, !2}}',
            '!1 = !{}', '!2 = !{!1, !1, !1, !1, !1, !1, !1, !3, !4, !5}',
            '!3 = !{i32 7, !"air.threadgroup_position_in_grid"}',
            '!4 = !{i32 8, !"air.threads_per_threadgroup"}',
            '!5 = !{i32 9, !"air.thread_position_in_threadgroup"}']
    return '\n'.join(out)+'\n'


def check(driver, output):
    output=Path(output);output.mkdir()
    llvm,env=paths();env['COMPAT_LOGICAL32']='mps-sift-32x1x1'
    rows=[]
    def run(name,text,changed,environment=None):
        source=output/(name+'.ll');fixed=output/(name+'-fixed.ll');source.write_text(text)
        p=subprocess.run([str(driver),str(llvm),str(source),str(fixed)],env=environment or env,capture_output=True,text=True)
        (output/(name+'.log')).write_text(p.stdout+p.stderr)
        assert p.returncode==0,(name,p.stdout,p.stderr)
        result=json.loads(p.stdout)
        assert result['changed']==changed,(name,result,p.stderr)
        assert result['original_unchanged'] and result['valid'],(name,result)
        result['name']=name;rows.append(result)
        transformed=fixed.read_text()
        if changed:
            assert 'call' in transformed and 'call void @air.wg.barrier' in transformed
            assert not any('call' in line and '@air.simd' in line for line in transformed.splitlines())
            assert 'addrspace(3) global [32 x i32]' in transformed
            assert 'addrspace(3) global [32 x <4 x float>]' in transformed
        return transformed
    run('zero',fixture(),True)
    full=fixture(True);fixed=run('full',full,True)
    run('already-translated',fixed,False)
    run('disabled',full,False,{k:v for k,v in env.items() if k!='COMPAT_LOGICAL32'})
    run('unknown-mode',full,False,{**env,'COMPAT_LOGICAL32':'generic'})
    run('other-kernel',full.replace(FUNCTION,'other'),False)
    run('render',full.replace('!air.kernel','!air.fragment'),False)
    run('lane-abi',full.replace('!"air.thread_position_in_threadgroup"','!"air.thread_position_in_grid"'),False)
    run('shape-abi',full.replace('!"air.threads_per_threadgroup"','!"air.grid_size"'),False)
    run('extra-definition',full+'define void @other() {ret void}\n',False)
    run('max-extra-consumer',full.replace(' %fold0 =',' store float %max0, float* %out\n %fold0 ='),False)
    run('max-wrong-fold',full.replace('@air.fast_fmax.f32(float %local0, float %max0)', '@air.fast_fmax.f32(float %v0, float %max0)'),False)
    run('max-wrong-neutral',full.replace(NEUTRAL,'0.0'),False)
    run('exit-extra-consumer',full.replace(' %result0 =',' store float %exitmax0, float* %out\n %result0 ='),False)
    run('exit-wrong-source',full.replace('float %exitmax0, i16 0','float %exitmax0, i16 1'),False)
    run('wrong-barrier',full.replace('barrier(i32 2, i32 4)','barrier(i32 1, i32 4)'),False)
    run('wrong-shuffle',full.replace('zeroinitializer, i16 1','zeroinitializer, i16 2'),False)
    run('varying-outer-control',full.replace('br i1 %active0','br i1 %vary'),False)
    # A varying phi selecting uniform constants must not count as a uniform branch.
    branch=''' br i1 %vary, label %yes, label %no
yes:
 br label %join
no:
 br label %join
join:
 %choice = phi i1 [true, %yes], [false, %no]
'''
    text=full.replace(' br label %pre0',branch+' br label %pre0',1).replace('br i1 %active0','br i1 %choice')
    run('varying-phi-control',text,False)
    run('varying-broadcast-source',full.replace('float 1.0, i16 0','float 1.0, i16 %lane'),False)
    run('wrong-histogram-stride',full.replace('add i16 %index0, 32','add i16 %index0, 8'),False)
    run('wrong-histogram-start',full.replace('%start0 = icmp ult i16 %lane, 36','%start0 = icmp ult i16 1, 36'),False)
    run('different-histogram-bound',full.replace('%more0 = icmp ult i16 %next0, 36','%more0 = icmp ult i16 %next0, 40'),False)
    run('reversed-histogram-branch',full.replace('br i1 %start0, label %loop0, label %exit0',
                                              'br i1 %start0, label %exit0, label %loop0'),False)
    run('missing-neighbor-exclusion',full.replace('icmp ult i16 %lane, 31','icmp ult i16 %lane, 32'),False)
    run('undef-branch',full.replace('br i1 %active0','br i1 undef'),False)
    run('closed-cycle',fixture().replace(' ret void',' br label %closed\nclosed:\n br label %closed'),False)
    unknown=full.replace(' %active0 = icmp ne i64 %ballot0, 0',
                         ' %active0 = call i1 @unknown()')+'declare i1 @unknown() readnone\n'
    run('unknown-pure-is-not-uniform',unknown,False)
    run('unexpected-call-count',full.replace(' %s5 = call <4 x float> @air.simd_shuffle_down.v4f32(<4 x float> zeroinitializer, i16 1)\n',''),False)
    (output/'results.json').write_text(json.dumps(rows,indent=2)+'\n')
    return rows
