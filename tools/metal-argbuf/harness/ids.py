# ids.py MODE JOBS|LIB [FN] OUT: list IGILIntrinsicInfo::getDeclaration IDs requested from IGILair_ldst_handling (stock runtime)
import os,sys,subprocess,shlex
from pathlib import Path
REPO=Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO/'cache/shaders/lib'))
import harness
STOCK=os.environ.get('ARGBUF_RT', str(REPO/'store/artifacts/shader-replay-runtime-r38-58517acfb9fa/runtime'))
WRAP=os.environ.get('ARGBUF_RT_WRAP')   # runtime copy whose dl/iCBE.dylib is the wrapper (see README, Host harness)
rt=STOCK
mode=sys.argv[1]
if mode=='render': args=[sys.argv[2],'-','render']; out=sys.argv[3]
else: args=[sys.argv[2],sys.argv[3],'compute']; out=sys.argv[4]
os.makedirs(out+'/cache',exist_ok=True)
e=harness.env(rt,None,out+'/cache',out=out,keys=False)
cmd=harness.cmd(rt)+args
envs=' '.join('-E %s'%shlex.quote(k+'='+v) for k,v in e.items() if k.startswith(('DYLD_','MTL_','RP_')))
script=f'''settings set target.process.stop-on-exec false
breakpoint set -r "IGILIntrinsicInfo::getDeclaration"
breakpoint command add 1 -s python -o "f=frame; print('ID', hex(f.FindRegister('rdx').GetValueAsUnsigned()), f.get_parent_frame().GetFunctionName() if f.get_parent_frame() else ''); return False"
process launch {envs} -- {' '.join(shlex.quote(c) for c in cmd[1:])}
quit
'''
open(out+'/ids.cmds','w').write(script)
r=subprocess.run(['lldb','--batch','-s',out+'/ids.cmds',cmd[0]],capture_output=True,text=True,timeout=600)
print('\n'.join(l for l in r.stdout.splitlines() if l.startswith('ID') or 'stop reason' in l))
