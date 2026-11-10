"""Exercise the live C transform with the pinned old LLVM and native CPU controls."""
import ctypes,json,os,re,subprocess,sys
from pathlib import Path
from replay_runtime import paths
def main():
    ROOT=Path(__file__).resolve().parent
    DRIVER=Path(sys.argv[1]).resolve();OUT=Path(sys.argv[2]).resolve();OUT.mkdir()
    LLVM,env=paths()
    rows=[]
    def run(name,text,changed=True):
     source=OUT/(name+'.ll');fixed=OUT/(name+'-fixed.ll');source.write_text(text)
     p=subprocess.run([str(DRIVER),str(LLVM),str(source),str(fixed)],env=env,capture_output=True,text=True)
     (OUT/(name+'.stderr')).write_text(p.stderr);(OUT/(name+'.stdout')).write_text(p.stdout)
     assert p.returncode==0,(name,p.stderr)
     result=json.loads(p.stdout);assert result['changed']==changed,(name,result)
     assert result['original_unchanged'] and result['valid'],(name,result)
     if changed:assert not result['remaining_pattern'],name
     rows.append(dict(name=name,**result));return fixed.read_text(),source,fixed

    def vector(n,value):return '<'+', '.join('i16 '+str(value) for _ in range(n))+'>'
    for n in (3,4,8,32):
     for op in ('add nuw nsw','sub nuw nsw','mul nuw nsw','udiv exact','sdiv exact','urem','srem','and','or','xor','icmp eq'):
      text=f'define void @probe(<{n} x half> %h) {{\n  %i = bitcast <{n} x half> %h to <{n} x i16>\n  %v = {op} <{n} x i16> %i, {vector(n,3)}, !compat.tag !0\n  ret void\n}}\n!0 = !{{!"retained"}}\n'
      out,_,_=run('op-'+str(n)+'-'+op.replace(' ','-'),text)
      assert len(re.findall(re.escape(op)+' i16',out))==n,(n,op)
      assert out.count('!compat.tag !')==n,(n,op)
    # More than the old prototype's 2048-instruction cap, plus vector width >16.
    text='define void @large(<32 x half> %h) {\n'+''.join(f'  %pad{i} = add i32 1, 2\n' for i in range(2200))+'  %i = bitcast <32 x half> %h to <32 x i16>\n  %v = and <32 x i16> %i, zeroinitializer\n  ret void\n}\n'
    run('large',text)
    run('unchanged','define i16 @plain(i16 %a) {\n  %v = xor i16 %a, 5\n  ret i16 %v\n}\n',False)
    # Constant-folded scalar values cannot carry the original instruction metadata;
    # refuse the clone and prove that the original survives byte for byte.
    run('refuse-constant-metadata','define void @refuse() {\n  %i = bitcast <3 x half> zeroinitializer to <3 x i16>, !compat.tag !0\n  %v = and <3 x i16> %i, zeroinitializer\n  ret void\n}\n!0 = !{!"retained"}\n',False)
    inputs=[]
    for n in (3,4,8,32):
     T=f'<{n} x i16>';H=f'<{n} x half>'
     lines=[f'define void @probe(i16* %src, i16* %dst) {{',f'  %p = bitcast i16* %src to {T}*',f'  %bits = load {T}, {T}* %p, align 2',f'  %h = bitcast {T} %bits to {H}',f'  %i = bitcast {H} %h to {T}']
     prev='%i'
     for index,(op,value) in enumerate((('and',-256),('or',257),('xor',-32768),('add',37),('sub',19),('mul',3),('udiv',7),('sdiv',3),('urem',97),('srem',31))):
      lines.append(f'  %v{index} = {op} {T} {prev}, {vector(n,value)}');prev='%v'+str(index)
     lines += [f'  %signed = sdiv {T} %i, {vector(n,3)}',f'  %rem = srem {T} %i, {vector(n,7)}',f'  %combined = xor {T} %signed, %rem',f'  %cmp = icmp ult {T} %i, {vector(n,-32768)}',f'  %selected = select <{n} x i1> %cmp, {T} {prev}, {T} %combined',f'  %back = bitcast {T} %selected to {H}',f'  %result = bitcast {H} %back to {T}',f'  %q = bitcast i16* %dst to {T}*',f'  store {T} %result, {T}* %q, align 2','  ret void','}']
     text='\n'.join(lines)+'\n';out,src,fixed=run('cpu-'+str(n),text)
     for suffix,body in (('original',text),('lowered',out)):
      p=OUT/(f'cpu-{n}-{suffix}.ll');p.write_text(body.replace('@probe(',f'@n{n}_{suffix}('));inputs.append(str(p))
    lib=OUT/'semantics.dylib'
    p=subprocess.run(['xcrun','clang','-O2','-dynamiclib',*inputs,'-o',str(lib)],capture_output=True,text=True)
    (OUT/'native-build.log').write_text(p.stdout+p.stderr);assert not p.returncode,p.stderr
    module=ctypes.CDLL(str(lib));tested=0
    for n in (3,4,8,32):
     functions=[getattr(module,f'n{n}_{v}') for v in ('original','lowered')]
     for f in functions:f.argtypes=[ctypes.POINTER(ctypes.c_uint16)]*2;f.restype=None
     Array=ctypes.c_uint16*(n+2);source=Array();a=Array();b=Array()
     for bits in range(65536):
      for k in range(n):source[k]=(bits+137*k)&65535
      a[n]=b[n]=0x1234;a[n+1]=b[n+1]=0x5678
      functions[0](source,a);functions[1](source,b)
      assert list(a)==list(b),(n,bits,list(a),list(b))
      assert list(a)[n:]==[0x1234,0x5678],(n,bits,'out-of-bounds output')
      tested+=n
    report=dict(classification='FACT',ir_controls=rows,native_lane_comparisons=tested,native_vectors=4*65536,native_acceptance=False,scope='CPU equivalence on all 65536 bit patterns per lane at four widths; not Intel GPU rendering')
    (OUT/'results.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(dict(ir_controls=len(rows),native_lane_comparisons=tested,native_vectors=4*65536)))

if __name__ == "__main__":
    main()
