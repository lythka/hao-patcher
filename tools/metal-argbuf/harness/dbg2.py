# dbg2.py LIB FN OUT NEWIGIL: like dbg.py, but replace the IGIL with the hand-edited file NEWIGIL before iCBE runs
import os,sys,subprocess,shlex
from pathlib import Path
REPO=Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO/'cache/shaders/lib'))
import harness
STOCK=os.environ.get('ARGBUF_RT', str(REPO/'store/artifacts/shader-replay-runtime-r38-58517acfb9fa/runtime'))
WRAP=os.environ.get('ARGBUF_RT_WRAP')   # runtime copy whose dl/iCBE.dylib is the wrapper (see README, Host harness)
rt=STOCK
lib,fn,out,newigil=sys.argv[1:5]
os.makedirs(out+'/cache',exist_ok=True)
e=harness.env(rt,None,out+'/cache',out=out,keys=False)
cmd=harness.cmd(rt)+[lib,fn,'compute']
envs=' '.join('-E %s'%shlex.quote(k+'='+v) for k,v in e.items() if k.startswith(('DYLD_','MTL_','RP_')))
py=out+'/swap.py'
open(py,'w').write(f'''
import lldb,struct
def swap(frame,bp_loc,d):
    p=frame.GetThread().GetProcess(); err=lldb.SBError()
    s=frame.FindRegister("rsi").GetValueAsUnsigned()
    data=open({newigil!r},"rb").read()
    buf=frame.EvaluateExpression("(char*)malloc(%d)" % (len(data)+64)).GetValueAsUnsigned()
    p.WriteMemory(buf, data+b"\\0", err)
    cap=(len(data)+64)|1
    p.WriteMemory(s, struct.pack("<QQQ", cap, len(data), buf), err)
    print("swapped IGIL", len(data), err.Success())
    return False
''')
script=f'''settings set target.process.stop-on-exec false
command script import {py}
breakpoint set -r "compileComputeFunctionIGILtoBinary"
breakpoint command add -F swap.swap 1
process launch {envs} -- {' '.join(shlex.quote(c) for c in cmd[1:])}
quit
'''
open(out+'/lldb.cmds','w').write(script)
r=subprocess.run(['lldb','--batch','-s',out+'/lldb.cmds',cmd[0]],capture_output=True,text=True,timeout=600)
print('\n'.join(l for l in (r.stdout+r.stderr).splitlines() if 'swapped' in l or 'JOB' in l or 'IGILtoBinary' in l or 'exited' in l or 'rror' in l))
