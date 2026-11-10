"""Exercise live null lowering, callback effects, refusals and native CPU values."""
import ctypes,hashlib,json,os,re,subprocess,sys
from pathlib import Path
from replay_runtime import paths
R=Path(__file__).resolve().parent;DRIVER=Path(sys.argv[1]).resolve();OUT=Path(sys.argv[2]).resolve();OUT.mkdir()
LLVM,env=paths()
layout='target datalayout = "e-p:64:64-p1:64:64"\n'
header='define i32 @probe(i8 addrspace(1)* %p, i32 %choose) readnone {\n'
end='  %r = zext i1 %c to i32\n  ret i32 %r\n}\n'
cases={}
for pred in ('eq','ne'):
 for swapped in (False,True):
  ops='null, %p' if swapped else '%p, null'
  cases[pred+str(int(swapped))]=layout+header+f'  %c = icmp {pred} i8 addrspace(1)* {ops}\n'+end
cases['select']=layout+header+'  %s = icmp ne i32 %choose, 0\n  %q = select i1 %s, i8 addrspace(1)* %p, i8 addrspace(1)* null\n  %c = icmp eq i8 addrspace(1)* %q, null\n'+end
cases['phi']=layout+header+'  %s = icmp ne i32 %choose, 0\n  br i1 %s, label %a, label %b\na:\n  br label %merge\nb:\n  br label %merge\nmerge:\n  %q = phi i8 addrspace(1)* [ %p, %a ], [ null, %b ]\n  %c = icmp eq i8 addrspace(1)* %q, null\n'+end
cases['bitcast']=layout+header+'  %q = bitcast i8 addrspace(1)* %p to i32 addrspace(1)*\n  %c = icmp eq i32 addrspace(1)* %q, null\n'+end
cases['gep']=layout+header+'  %n = sext i32 %choose to i64\n  %q = getelementptr i8, i8 addrspace(1)* %p, i64 %n\n  %c = icmp eq i8 addrspace(1)* %q, null\n'+end
cases['nested']=layout+header+'  %s = icmp ne i32 %choose, 0\n  %q = select i1 %s, i8 addrspace(1)* %p, i8 addrspace(1)* null\n  %r = call i32 @helper(i8 addrspace(1)* %q)\n  ret i32 %r\n}\ndefine internal i32 @helper(i8 addrspace(1)* %p) noinline readnone {\n  %r = call i32 @inner(i8 addrspace(1)* %p)\n  ret i32 %r\n}\ndefine internal i32 @inner(i8 addrspace(1)* %p) noinline readnone {\n  %c = icmp eq i8 addrspace(1)* %p, null\n'+end
cases['metadata']=cases['eq0'].replace('%p, null\n','%p, null, !compat.tag !0\n')+'!0 = !{!"retained"}\n'
cases['refuse-width']=cases['eq0'].replace('p1:64:64','p1:32:32')
cases['refuse-layout']=cases['eq0'].replace(layout,'')
cases['unchanged']=layout+header+'  ret i32 %choose\n}\n'
cases.update({'scoped': 'target datalayout = "e-p:64:64-p1:64:64"\ndefine i32 @probe(i8 addrspace(1)* %p, i32 %choose) readnone {\n  %r = call i32 @helper(i8 addrspace(1)* %p) readnone\n  ret i32 %r\n}\ndefine internal i32 @helper(i8 addrspace(1)* %q) noinline readnone {\n  %c = icmp eq i8 addrspace(1)* %q, null\n  %r = zext i1 %c to i32\n  ret i32 %r\n}\ndefine i32 @unrelated(i32 %x) readnone {\n  ret i32 %x\n}\n', 'optimizer-only': 'target datalayout = "e-p:64:64-p1:64:64"\ndefine i32 @probe(i8 addrspace(1)* %p, i32 %choose) readnone {\n  %r = call i32 @helper(i8 addrspace(1)* null) readnone\n  ret i32 %r\n}\ndefine internal i32 @helper(i8 addrspace(1)* %q) noinline readnone {\n  %c = icmp eq i8 addrspace(1)* %q, null\n  %r = zext i1 %c to i32\n  ret i32 %r\n}\n', 'optnone': 'target datalayout = "e-p:64:64-p1:64:64"\ndefine i32 @probe(i8 addrspace(1)* %p, i32 %choose) readnone {\n  %r = call i32 @helper(i8 addrspace(1)* %p) readnone\n  ret i32 %r\n}\ndefine internal i32 @helper(i8 addrspace(1)* %q) noinline optnone readnone {\n  %c = icmp eq i8 addrspace(1)* %q, null\n  %r = zext i1 %c to i32\n  ret i32 %r\n}\ndefine i32 @unrelated(i32 %x) readnone {\n  ret i32 %x\n}\n', 'recursive': 'target datalayout = "e-p:64:64-p1:64:64"\ndefine i32 @probe(i8 addrspace(1)* %p, i32 %choose) readnone {\n  %r = call i32 @rec(i8 addrspace(1)* %p, i32 %choose) readnone\n  ret i32 %r\n}\ndefine internal i32 @rec(i8 addrspace(1)* %q, i32 %n) noinline readnone {\n  %done = icmp sle i32 %n, 0\n  br i1 %done, label %end, label %loop\nloop:\n  %next = sub i32 %n, 1\n  %r = call i32 @rec(i8 addrspace(1)* %q, i32 %next) readnone\n  ret i32 %r\nend:\n  %c = icmp eq i8 addrspace(1)* %q, null\n  %v = zext i1 %c to i32\n  ret i32 %v\n}\n', 'escape': 'target datalayout = "e-p:64:64-p1:64:64"\n@fp = global i32 (i8 addrspace(1)*)* @helper\ndefine i32 @probe(i8 addrspace(1)* %p, i32 %choose) readnone {\n  %f = load i32 (i8 addrspace(1)*)*, i32 (i8 addrspace(1)*)** @fp\n  %r = call i32 %f(i8 addrspace(1)* %p) readnone\n  ret i32 %r\n}\ndefine internal i32 @helper(i8 addrspace(1)* %q) noinline readnone {\n  %c = icmp eq i8 addrspace(1)* %q, null\n  %r = zext i1 %c to i32\n  ret i32 %r\n}\n', 'mixed': 'target datalayout = "e-p:64:64-p1:64:64"\ndefine i32 @probe(i8 addrspace(1)* %p, i32 %choose) readnone {\n  %bits = trunc i32 %choose to i16\n  %half = bitcast i16 %bits to half\n  %h = insertelement <4 x half> zeroinitializer, half %half, i32 0\n  %b = bitcast <4 x half> %h to <4 x i16>\n  %x = and <4 x i16> %b, <i16 255, i16 255, i16 255, i16 255>\n  %v = extractelement <4 x i16> %x, i32 0\n  %w = zext i16 %v to i32\n  %n = call i32 @helper(i8 addrspace(1)* %p) readnone\n  %r = add i32 %w, %n\n  ret i32 %r\n}\ndefine internal i32 @helper(i8 addrspace(1)* %q) noinline readnone {\n  %c = icmp eq i8 addrspace(1)* %q, null\n  %r = zext i1 %c to i32\n  ret i32 %r\n}\n', 'private-callback': 'target datalayout = "e-p:64:64-p1:64:64"\n@fp = internal global i32 (i8 addrspace(1)*)* @helper\ndefine i32 @probe(i8 addrspace(1)* %p, i32 %choose) readnone {\n  %f = load volatile i32 (i8 addrspace(1)*)*, i32 (i8 addrspace(1)*)** @fp\n  %r = call i32 %f(i8 addrspace(1)* %p) readnone\n  ret i32 %r\n}\ndefine internal i32 @helper(i8 addrspace(1)* %q) noinline readnone {\n  %c = icmp eq i8 addrspace(1)* %q, null\n  %r = zext i1 %c to i32\n  ret i32 %r\n}\n', 'private-slot': 'target datalayout = "e-p:64:64-p1:64:64"\ndefine i32 @probe(i8 addrspace(1)* %p, i32 %choose) readnone {\n  %slot = alloca i32 (i8 addrspace(1)*)*\n  store i32 (i8 addrspace(1)*)* @helper, i32 (i8 addrspace(1)*)** %slot\n  %f = load volatile i32 (i8 addrspace(1)*)*, i32 (i8 addrspace(1)*)** %slot\n  %r = call i32 %f(i8 addrspace(1)* %p) readnone\n  ret i32 %r\n}\ndefine internal i32 @helper(i8 addrspace(1)* %q) noinline readnone {\n  %c = icmp eq i8 addrspace(1)* %q, null\n  %r = zext i1 %c to i32\n  ret i32 %r\n}\n', 'parameter-escape': 'target datalayout = "e-p:64:64-p1:64:64"\ndefine i32 @probe(i8 addrspace(1)* %p, i32 (i8 addrspace(1)*)** %slot) {\n  store i32 (i8 addrspace(1)*)* @helper, i32 (i8 addrspace(1)*)** %slot\n  %f = load i32 (i8 addrspace(1)*)*, i32 (i8 addrspace(1)*)** %slot\n  %r = call i32 %f(i8 addrspace(1)* %p) readnone\n  ret i32 %r\n}\ndefine internal i32 @helper(i8 addrspace(1)* %q) noinline readnone {\n  %c = icmp eq i8 addrspace(1)* %q, null\n  %r = zext i1 %c to i32\n  ret i32 %r\n}\n', 'direct-call-attrs': 'target datalayout = "e-p:64:64-p1:64:64"\ndefine i32 @probe(i8 addrspace(1)* %p, i32 %choose) readnone {\n  %r = call i32 @helper(i8 addrspace(1)* %p) readnone\n  ret i32 %r\n}\ndefine i32 @helper(i8 addrspace(1)* %q) noinline readnone {\n  %c = icmp eq i8 addrspace(1)* %q, null\n  %r = zext i1 %c to i32\n  ret i32 %r\n}\ndefine i32 @unrelated(i32 %x) readnone {\n  ret i32 %x\n}\n', 'private-array': 'target datalayout = "e-p:64:64-p1:64:64"\n@fp = internal global [2 x i32 (i8 addrspace(1)*)*] [i32 (i8 addrspace(1)*)* @helper, i32 (i8 addrspace(1)*)* @other]\ndefine i32 @probe(i8 addrspace(1)* %p, i32 %choose) readnone {\n  %idx = and i32 %choose, 1\n  %slot = getelementptr [2 x i32 (i8 addrspace(1)*)*], [2 x i32 (i8 addrspace(1)*)*]* @fp, i32 0, i32 %idx\n  %f = load volatile i32 (i8 addrspace(1)*)*, i32 (i8 addrspace(1)*)** %slot\n  %r = call i32 %f(i8 addrspace(1)* %p) readnone\n  ret i32 %r\n}\ndefine internal i32 @helper(i8 addrspace(1)* %q) noinline readnone {\n  %c = icmp eq i8 addrspace(1)* %q, null\n  %r = zext i1 %c to i32\n  ret i32 %r\n}\ndefine internal i32 @other(i8 addrspace(1)* %p) readnone { ret i32 17 }\n', 'constant-array-slot': 'target datalayout = "e-p:64:64-p1:64:64"\n@fp = internal global [2 x i32 (i8 addrspace(1)*)*] [i32 (i8 addrspace(1)*)* @helper, i32 (i8 addrspace(1)*)* @other]\ndefine i32 @probe(i8 addrspace(1)* %p, i32 %choose) readnone {\n  %idx = and i32 %choose, 1\n  %slot = bitcast i32 (i8 addrspace(1)*)** getelementptr ([2 x i32 (i8 addrspace(1)*)*], [2 x i32 (i8 addrspace(1)*)*]* @fp, i32 0, i32 0) to i32 (i8 addrspace(1)*)**\n  %f = load volatile i32 (i8 addrspace(1)*)*, i32 (i8 addrspace(1)*)** %slot\n  %r = call i32 %f(i8 addrspace(1)* %p) readnone\n  ret i32 %r\n}\ndefine internal i32 @helper(i8 addrspace(1)* %q) noinline readnone {\n  %c = icmp eq i8 addrspace(1)* %q, null\n  %r = zext i1 %c to i32\n  ret i32 %r\n}\ndefine internal i32 @other(i8 addrspace(1)* %p) readnone { ret i32 17 }\n', 'external-callback': 'target datalayout = "e-p:64:64-p1:64:64"\ndefine i32 @probe(i8 addrspace(1)* %p, i32 %choose) readnone {\n  %r = call i32 @invoke(i32 (i8 addrspace(1)*)* @helper, i8 addrspace(1)* %p) readnone\n  ret i32 %r\n}\ndefine internal i32 @helper(i8 addrspace(1)* %q) noinline readnone {\n  %c = icmp eq i8 addrspace(1)* %q, null\n  %r = zext i1 %c to i32\n  ret i32 %r\n}\ndefine i32 @unrelated(i32 %x) readnone {\n  ret i32 %x\n}\ndeclare i32 @invoke(i32 (i8 addrspace(1)*)*, i8 addrspace(1)*) readnone\n', 'cast-call': 'target datalayout = "e-p:64:64-p1:64:64"\ndefine i32 @probe(i8 addrspace(1)* %p, i32 %choose) readnone {\n  %r = call i32 bitcast (i32 (i32 addrspace(1)*)* @helper to i32 (i8 addrspace(1)*)*)(i8 addrspace(1)* %p) readnone\n  ret i32 %r\n}\ndefine i32 @helper(i32 addrspace(1)* %q) noinline readnone {\n  %c = icmp eq i32 addrspace(1)* %q, null\n  %r = zext i1 %c to i32\n  ret i32 %r\n}\ndefine i32 @unrelated(i32 %x) readnone {\n  ret i32 %x\n}\n', 'untouched-external': 'target datalayout = "e-p:64:64-p1:64:64"\ndefine i32 @probe(i8 addrspace(1)* %p, i32 %choose) readnone {\n  %r = call i32 @helper(i8 addrspace(1)* %p) readnone\n  ret i32 %r\n}\ndefine i32 @helper(i8 addrspace(1)* %q) noinline readnone {\n  %c = icmp eq i8 addrspace(1)* %q, null\n  %r = zext i1 %c to i32\n  ret i32 %r\n}\ndefine i32 @unrelated(i32 %x) readnone {\n  ret i32 %x\n}\ndeclare i32 @unrelated_external(i32) readnone\ndefine i32 @unrelated_caller(i32 %x) readnone {\n  %r = call i32 @unrelated_external(i32 %x) readnone\n  ret i32 %r\n}\n'})
rows=[];cpu=[]
for name,text in cases.items():
 src=OUT/(name+'.ll');fixed=OUT/(name+'-fixed.ll');src.write_text(text)
 p=subprocess.run([str(DRIVER),str(LLVM),str(src),str(fixed)],env=env,capture_output=True,text=True)
 (OUT/(name+'.stderr')).write_text(p.stderr);(OUT/(name+'.stdout')).write_text(p.stdout)
 assert p.returncode==0,(name,p.stderr)
 result=json.loads(p.stdout);assert result['valid'] and result['original_unchanged'],(name,result)
 expected=not name.startswith(('refuse','unchanged')) and name not in ('escape','parameter-escape','external-callback');assert result['changed']==expected,(name,result)
 if expected:
  assert not result['remaining_null'] and not result['remaining_pattern'],(name,result)
  assert not re.search(r'icmp (?:eq|ne) [^\n]*addrspace\(1\)\*[^\n]*null',fixed.read_text()),name
  if name=='metadata':assert '!compat.tag' in fixed.read_text()
  if name=='untouched-external':
   rows.append(dict(name=name,**result));continue
  inputs=[]
  for suffix,body in [('original',text),('fixed',fixed.read_text())]:
   path=OUT/(name+'-'+suffix+'-native.ll');path.write_text(re.sub(r'@([A-Za-z_][A-Za-z_0-9]*)',lambda m: '@'+suffix+'_'+m[1],body));inputs.append(str(path))
  lib=OUT/(name+'.dylib');p=subprocess.run(['xcrun','clang','-O2','-dynamiclib',*inputs,'-o',str(lib)],capture_output=True,text=True)
  (OUT/(name+'-native-build.log')).write_text(p.stdout+p.stderr);assert p.returncode==0,(name,p.stderr)
  dll=ctypes.CDLL(str(lib));funcs=[dll.original_probe,dll.fixed_probe]
  for f in funcs:f.argtypes=[ctypes.c_void_p,ctypes.c_int32];f.restype=ctypes.c_int32
  count=0
  for bits in [0,1,2,4,255,256,1<<32,1<<63,(1<<64)-1,*[(i*0x9e3779b97f4a7c15)&((1<<64)-1) for i in range(1024)]]:
   for choose in [-1,0,1,2,255]:
    a,b=[f(bits,choose) for f in funcs];assert a==b,(name,bits,choose,a,b);count+=1
  cpu.append(dict(name=name,cases=count))
 rows.append(dict(name=name,**result))
def attrs(text,line):
 match=re.search(r'#(\d+)',line)
 return line+(' '+re.search(r'^attributes #'+match[1]+r' = (.*)$',text,re.M)[1] if match else '')
for name in ('scoped','direct-call-attrs','untouched-external'):
 text=(OUT/(name+'-fixed.ll')).read_text()
 line=next(x for x in text.splitlines() if x.startswith('define ') and '@unrelated(' in x)
 assert 'readnone' in attrs(text,line),(name,'unrelated contract lost')
 if name=='untouched-external':
  for line in text.splitlines():
   if '@unrelated_external(' in line or '@unrelated_caller(' in line:assert 'readnone' in attrs(text,line),line
for name in ('private-callback','private-slot','private-array','constant-array-slot','direct-call-attrs','cast-call','recursive'):
 text=(OUT/(name+'-fixed.ll')).read_text()
 for line in text.splitlines():
  if 'call i32 ' in line:assert 'readnone' not in attrs(text,line),(name,line)
text=(OUT/'optnone-fixed.ll').read_text()
line=next(x for x in text.splitlines() if x.startswith('define ') and '@helper(' in x)
assert 'optnone' in attrs(text,line) and 'noinline' in attrs(text,line)
assert 'compat.null.slot' not in (OUT/'optimizer-only-fixed.ll').read_text()
report=dict(classification='FACT',native_acceptance=False,ir=rows,cpu=cpu,cpu_comparisons=sum(x['cases'] for x in cpu),source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
(OUT/'results.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(dict(ir=len(rows),cpu_comparisons=report['cpu_comparisons'])))
