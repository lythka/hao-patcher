# run.py LIB FN OUT: compile compute function FN through the stock runtime
import os,sys,subprocess,shlex
from pathlib import Path
REPO=Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO/'cache/shaders/lib'))
import harness
STOCK=os.environ.get('ARGBUF_RT', str(REPO/'store/artifacts/shader-replay-runtime-r38-58517acfb9fa/runtime'))
WRAP=os.environ.get('ARGBUF_RT_WRAP')   # runtime copy whose dl/iCBE.dylib is the wrapper (see README, Host harness)
rt=STOCK
lib,fn,out=sys.argv[1:4]
os.makedirs(out,exist_ok=True); cache=os.path.join(out,'cache'); os.makedirs(cache,exist_ok=True)
e=harness.env(rt,None,cache,out=out,keys=False,extra={'MTL_IG_SHADER_DUMP_ENABLE':'1'})
r=subprocess.run(harness.cmd(rt)+[lib,fn,'compute'],env=e,capture_output=True,text=True,timeout=300)
print('exit',r.returncode); print(r.stdout[-3000:]); print(r.stderr[-3000:])
