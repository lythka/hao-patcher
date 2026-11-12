# dbg.py LIB FN OUT IGILFILE: compile compute function FN (stock runtime) under lldb and save the IGIL text the
# plugin hands to iCBE (MTLIntelCompiler::compileComputeFunctionIGILtoBinary, rsi = std::string)
import os,sys,subprocess,shlex
from pathlib import Path
REPO=Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO/'cache/shaders/lib'))
import harness
STOCK=os.environ.get('ARGBUF_RT', str(REPO/'store/artifacts/shader-replay-runtime-r38-58517acfb9fa/runtime'))
WRAP=os.environ.get('ARGBUF_RT_WRAP')   # runtime copy whose dl/iCBE.dylib is the wrapper (see README, Host harness)
rt=STOCK
lib,fn,out,igil=sys.argv[1:5]
os.makedirs(out+'/cache',exist_ok=True)
e=harness.env(rt,None,out+'/cache',out=out,keys=False)
cmd=harness.cmd(rt)+[lib,fn,'compute']
envs=' '.join('-E %s'%shlex.quote(k+'='+v) for k,v in e.items() if k.startswith(('DYLD_','MTL_','RP_')))
script=f'''settings set target.process.stop-on-exec false
breakpoint set -n "MTLIntelCompiler::compileComputeFunctionIGILtoBinary(std::__1::basic_string<char, std::__1::char_traits<char>, std::__1::allocator<char> >&, MTLIntelCSDeviceBinary*, unsigned int, std::__1::basic_string<char, std::__1::char_traits<char>, std::__1::allocator<char> >&)"
breakpoint set -r "compileComputeFunctionIGILtoBinary"
process launch {envs} -- {' '.join(shlex.quote(c) for c in cmd[1:])}
script import lldb; f=lldb.frame; s=f.FindRegister("rsi").GetValueAsUnsigned(); p=lldb.process; e=lldb.SBError(); b=p.ReadMemory(s,24,e); import struct; w=struct.unpack("<QQQ",b); \
  (lp,ln)=((w[2],w[1]) if (b[0]&1) else (s+1,b[0]>>1)); d=p.ReadMemory(lp,ln,e); open({igil!r},"wb").write(d); print("IGIL bytes",ln)
kill
quit
'''
open(out+'/lldb.cmds','w').write(script)
r=subprocess.run(['lldb','--batch','-s',out+'/lldb.cmds',cmd[0]],capture_output=True,text=True,timeout=600)
print(r.stdout[-2500:]); print(r.stderr[-1500:])
