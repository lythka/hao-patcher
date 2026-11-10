"""Host-only 3802 replay of both MPS controls through the runtime compiler pass.

No GPU submission; uses the stored replay harness's synthetic device. The GPU
client adapter is deliberately absent. Each control gets a new private cache.
"""
import argparse
import json
from pathlib import Path
import re
import subprocess
import sys

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
sys.path.insert(0,str(HERE.parents[1]/'shaders/lib'))
import harness
from record import record


def run(output,runtime,payload,translator,library):
    output,runtime,payload,translator,library=(p.resolve() for p in (output,runtime,payload,translator,library))
    output.mkdir(parents=True)
    results=[]
    for n,logical,blocks in ((0,4,8),(1,66,12)):
        out=output/f'mps-{n}';out.mkdir();(out/'cache').mkdir()
        env=harness.env(str(runtime),None,str(out/'cache'),str(out),keys=False,
                        extra={'RP_ROOT':str(payload),'RP_SLEEP':'0','RP_FC':f'124.33={n};127.33={n}',
                               'COMPAT_BLOCK_READS':'per-axis-mip0','COMPAT_CLIENT_ABI':'axis-st-v1',
                               'COMPAT_LOGICAL32':'mps-sift-32x1x1','COMPAT_DUMP':str(out)})
        env['DYLD_INSERT_LIBRARIES']+=':'+str(translator/'compat.dylib')
        command=harness.cmd(str(runtime))+[str(library),'ExtractFeaturePointsAndOrientationFusedMultifetch','compute']
        p=subprocess.run(command,env=env,capture_output=True,text=True,timeout=120)
        (out/'stderr.log').write_text(p.stderr);(out/'stdout.log').write_text(p.stdout)
        p.check_returncode()
        expected=(f'[compat] logical32={logical}',f'[compat] block_reads={blocks}',
                  'verified=1','pipeline=OK','jobs ok=1 bad=0')
        if not all(s in p.stderr for s in expected):raise ValueError('compile did not exercise both runtime passes: '+p.stderr)
        lowered=(out/'module-000-lowered.ll').read_text()
        if re.search(r'call[^\n]*@air.simd',lowered):raise ValueError('native SIMD call remained')
        results.append(dict(constants=env['RP_FC'],logical32=logical,block_reads=blocks,
                            pipeline='OK',gpu_execution=False,command=command))
    (output/'results.json').write_text(json.dumps(results,indent=2)+'\n')
    inputs=[library,translator/'build.json',runtime.parent/'build.json',payload/'build.json']
    inputs += [p for p in translator.iterdir() if p.suffix=='.dylib']
    record(output,inputs)
    return results


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('output',type=Path)
    for key in ('runtime','payload','translator','library'):p.add_argument('--'+key,type=Path,required=True)
    a=p.parse_args();print(json.dumps(run(a.output,a.runtime,a.payload,a.translator,a.library),indent=2))
