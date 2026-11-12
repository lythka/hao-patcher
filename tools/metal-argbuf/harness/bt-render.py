# bt-render.py JOBS OUTDIR: run render jobs (stock runtime) under lldb and print the backtrace of a crash
import os,sys,subprocess,shlex
from pathlib import Path
REPO=Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO/'cache/shaders/lib'))
import harness
STOCK=os.environ.get('ARGBUF_RT', str(REPO/'store/artifacts/shader-replay-runtime-r38-58517acfb9fa/runtime'))
WRAP=os.environ.get('ARGBUF_RT_WRAP')   # runtime copy whose dl/iCBE.dylib is the wrapper (see README, Host harness)
rt=STOCK
jobs,out=sys.argv[1:3]
os.makedirs(out+'/cache',exist_ok=True)
e=harness.env(rt,None,out+'/cache',out=out,keys=False)
cmd=harness.cmd(rt)+[jobs,'-','render']
envs=' '.join('-E %s'%shlex.quote(k+'='+v) for k,v in e.items() if k.startswith(('DYLD_','MTL_','RP_')))
script=f'''settings set target.process.stop-on-exec false
process launch {envs} -- {' '.join(shlex.quote(c) for c in cmd[1:])}
bt 40
register read rip rdi rsi rax
kill
quit
'''
open(out+'/bt.cmds','w').write(script)
r=subprocess.run(['lldb','--batch','-s',out+'/bt.cmds',cmd[0]],capture_output=True,text=True,timeout=600)
print(r.stdout[-6000:])
