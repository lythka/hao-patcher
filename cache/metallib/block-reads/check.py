"""Measure a rejected block-read candidate; native sampler semantics still fail."""
from pathlib import Path
import array
import datetime
import hashlib
import itertools
import json
import re
import subprocess
import sys
HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE.parent/'lib'))
import block_reads


def verify(data, outputs, log, report):
    results=[]
    for row in json.loads((data/'results.json').read_text()):
        name=row['case']+'-fixed'
        section=log.read_text().split(name+'.metallib\n',1)[1].split('.metallib\n',1)[0]
        device=json.loads(re.search(r'\{"device"[^\n]+',section)[0])
        expected=(data/(row['case']+'-original.out')).read_bytes()
        actual=(outputs/(name+'.out')).read_bytes()
        result=dict(case=row['case'],**device,scalar_comparisons=len(expected)//4,equal=actual==expected)
        results.append(result)
        print(json.dumps(result),flush=True)
    report.write_text(json.dumps(results,indent=2)+'\n')
    if not all(r['equal'] for r in results):raise ValueError('native block-read output differs')
    return results


def check(output, revision):
    output=Path(output).resolve();output.mkdir(parents=True)
    commands=[];results=[]
    def run(args):
        args=list(map(str,args));p=subprocess.run(args,capture_output=True,text=True)
        commands.append(dict(args=args,returncode=p.returncode,stdout=p.stdout,stderr=p.stderr))
        (output/'commands.json').write_text(json.dumps(commands,indent=2)+'\n')
        p.check_returncode();return p.stdout
    run(['xcrun','clang','-O2','-fobjc-arc','-framework','Foundation','-framework','Metal',HERE/'probe.m','-o',output/'probe'])
    run(['codesign','-s','-',output/'probe'])
    # The private MetalOld runtime resolves these compiler callbacks in the
    # executable. Reuse the replay bridge, retaining the real native GPU device.
    bridge=HERE.parents[1]/'shaders/src/replay.m'
    run(['xcrun','clang','-arch','x86_64','-mmacosx-version-min=13.0','-O1','-Dmain=unused_replay_main',
         '-c',bridge,'-o',output/'bridge.o'])
    run(['xcrun','clang','-arch','x86_64','-mmacosx-version-min=13.0','-O2','-fobjc-arc',
         '-framework','Foundation','-framework','Metal',HERE/'probe.m',output/'bridge.o','-o',output/'probe-intel'])
    run(['codesign','-s','-',output/'probe-intel'])
    points=list(itertools.product(range(-3,13),range(-3,11),range(3),range(4)))
    assert len(points)%32==0
    (output/'coords.bin').write_bytes(array.array('i',[v for point in points for v in point]).tobytes())
    for width,height in [(2,1),(1,2)]:
        for address in ['clamp_to_edge','clamp_to_zero']:
            for x,y in [(0,0),(0,3),(-1,-2)]:
                label=f'{width}x{height}-{address}-{x}-{y}';path=output/label
                s=(HERE/'probe.metal').read_text()
                for k,v in dict(WIDTH=width,HEIGHT=height,ADDRESS=address,OFFSET_X=x,OFFSET_Y=y,SECOND_X=width-1,SECOND_Y=height-1).items():s=s.replace(k,str(v))
                path.with_suffix('.metal').write_text(s)
                run(['xcrun','metal','-std=metal4.1','-mmacos-version-min=27.0','-c',path.with_suffix('.metal'),'-o',path.with_suffix('.air')])
                text=run(['xcrun','metal-objdump','--disassemble',path.with_suffix('.air')]);text=text[text.index('; ModuleID'):]
                fixed,report=block_reads.rewrite(text);assert report['calls']==1
                outputs=[]
                for variant,ir in [('original',text),('fixed',fixed)]:
                    p=output/(label+'-'+variant);p.with_suffix('.ll').write_text(ir)
                    run(['xcrun','metal-as',p.with_suffix('.ll'),'-o',p.with_suffix('.air')])
                    run(['xcrun','metallib',p.with_suffix('.air'),'-o',p.with_suffix('.metallib')])
                    device=json.loads(run([output/'probe',p.with_suffix('.metallib'),output/'coords.bin',p.with_suffix('.out')]))
                    outputs.append(p.with_suffix('.out').read_bytes())
                row=dict(case=label,**device,scalar_comparisons=len(outputs[0])//4,equal=outputs[0]==outputs[1]);results.append(row)
                print(json.dumps(row),flush=True);(output/'results.json').write_text(json.dumps(results,indent=2)+'\n')
                if not row['equal']:raise ValueError('block-read GPU mismatch: '+label)
    inputs=[Path(__file__),HERE/'probe.m',HERE/'probe.metal',Path(block_reads.__file__),
            bridge,bridge.with_name('memo.h'),bridge.with_name('capture-replay.m')]
    report=dict(unit='cache/metallib',revision=revision,commit=run(['git','rev-parse','HEAD']).strip(),
                date=datetime.datetime.now(datetime.timezone.utc).isoformat(),inputs=[dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest()) for p in inputs],
                results=results,intel_gpu_acceptance=False)
    (output/'build.json').write_text(json.dumps(report,indent=2)+'\n')
    return report
