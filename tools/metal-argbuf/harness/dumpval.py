# dumpval.py JOBS OUT ADDR SLOT: render jobs (stock runtime) under lldb; at libIGIL-Metal file address ADDR dump the
# raw words of the llvm::Value* stored at [rbp+SLOT] (e.g. -0x168), its Type and the Type's first contained type
import os,sys,subprocess,shlex
from pathlib import Path
REPO=Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO/'cache/shaders/lib'))
import harness
rt=os.environ.get('ARGBUF_RT', str(REPO/'store/artifacts/shader-replay-runtime-r38-58517acfb9fa/runtime'))
jobs,out,addr,slot=sys.argv[1:5]
os.makedirs(out+'/cache',exist_ok=True)
e=harness.env(rt,None,out+'/cache',out=out,keys=False)
cmd=harness.cmd(rt)+[jobs,'-','render']
envs=' '.join('-E %s'%shlex.quote(k+'='+v) for k,v in e.items() if k.startswith(('DYLD_','MTL_','RP_')))
dump='((void(*)(void*))dlsym((void*)-2,"_ZNK4llvm5Value4dumpEv"))'
script=f'''settings set target.process.stop-on-exec false
process launch --stop-at-entry {envs} -- {' '.join(shlex.quote(c) for c in cmd[1:])}
breakpoint set -n "(anonymous namespace)::IGILair_ldst_handling::ProcessFunction(llvm::Function&)"
continue
breakpoint set -a `(long)$pc - (long)0x7fff60c1af88 + (long){addr}`
continue
register read rbx rax
memory read -fx -s8 -c4 `*(long*)($rbp{slot})`
memory read -fx -s8 -c4 `**(long**)($rbp{slot})`
memory read -fx -s8 -c4 `*(long*)((**(long**)($rbp{slot}))+0x10)`
memory read -fx -s8 -c4 `*(long*)(*(long*)((**(long**)($rbp{slot}))+0x10))`
bt 3
kill
quit
'''
open(out+'/dv.cmds','w').write(script)
r=subprocess.run(['lldb','--batch','-s',out+'/dv.cmds',cmd[0]],capture_output=True,text=True,timeout=600)
print(r.stdout[-5000:]); print(r.stderr[-2000:])
