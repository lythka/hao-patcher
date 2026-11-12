# run-render.py JOBS OUTDIR [stock]: compile render jobs (replay.m format) through the wrapped (default) or stock runtime
import os,sys,subprocess,shlex
from pathlib import Path
REPO=Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO/'cache/shaders/lib'))
import harness
STOCK=os.environ.get('ARGBUF_RT', str(REPO/'store/artifacts/shader-replay-runtime-r38-58517acfb9fa/runtime'))
WRAP=os.environ.get('ARGBUF_RT_WRAP')   # runtime copy whose dl/iCBE.dylib is the wrapper (see README, Host harness)
rt=STOCK if len(sys.argv)>3 and sys.argv[3]=='stock' else WRAP
jobs,out=sys.argv[1:3]
os.makedirs(out+'/cache',exist_ok=True)
e=harness.env(rt,None,out+'/cache',out=out,keys=False)
r=subprocess.run(harness.cmd(rt)+[jobs,'-','render'],env=e,capture_output=True,text=True,timeout=300)
print('exit',r.returncode); print(r.stdout[-3000:]); print(r.stderr[-3000:])
