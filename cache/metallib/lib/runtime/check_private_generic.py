"""Private AS8 specialization: old-LLVM structure, refusals and CPU semantics."""
import ctypes
import json
from pathlib import Path
import re
import subprocess
import sys

from replay_runtime import paths

LAYOUT = 'target datalayout = "e-p:64:64-p1:64:64-p8:64:64"\n'
HEADER = 'define i64 @probe(i64 %value, i32 %index) {\n'
SETUP = '''  %a = alloca [4 x i64], align 16
  %i = and i32 %index, 3
  %idx = zext i32 %i to i64
  %p = getelementptr inbounds [4 x i64], [4 x i64]* %a, i64 0, i64 %idx
  store i64 %value, i64* %p, align 8
'''
CASES = {
    'direct': SETUP + '  %g = addrspacecast i64* %p to i64 addrspace(8)*\n'
              '  %v = load i64, i64 addrspace(8)* %g, align 8\n',
    'generic-gep': SETUP + '  %g = addrspacecast [4 x i64]* %a to [4 x i64] addrspace(8)*\n'
                   '  %q = getelementptr inbounds [4 x i64], [4 x i64] addrspace(8)* %g, i64 0, i64 %idx\n'
                   '  %v = load i64, i64 addrspace(8)* %q, align 8\n',
    'generic-bitcast': SETUP + '  %b = bitcast i64* %p to i8*\n'
                       '  %g = addrspacecast i8* %b to i8 addrspace(8)*\n'
                       '  %q = bitcast i8 addrspace(8)* %g to i64 addrspace(8)*\n'
                       '  %v = load i64, i64 addrspace(8)* %q, align 8\n',
    'volatile-metadata': SETUP + '  %g = addrspacecast i64* %p to i64 addrspace(8)*\n'
                         '  %v = load volatile i64, i64 addrspace(8)* %g, align 4, !compat.tag !0\n',
    'atomic': SETUP + '  %g = addrspacecast i64* %p to i64 addrspace(8)*\n'
              '  %v = load atomic i64, i64 addrspace(8)* %g monotonic, align 8\n',
}
CONTROLS = {
    'device': '''define i64 @probe(i64 addrspace(1)* %p) {
  %g = addrspacecast i64 addrspace(1)* %p to i64 addrspace(8)*
  %v = load i64, i64 addrspace(8)* %g, align 8
  ret i64 %v
}

''',
    'threadgroup': '''define i64 @probe(i64 addrspace(3)* %p) {
  %g = addrspacecast i64 addrspace(3)* %p to i64 addrspace(8)*
  %v = load i64, i64 addrspace(8)* %g, align 8
  ret i64 %v
}
''',
    'argument': '''define i64 @probe(i64* %p) {
  %g = addrspacecast i64* %p to i64 addrspace(8)*
  %v = load i64, i64 addrspace(8)* %g, align 8
  ret i64 %v
}
''',
    'mixed': '''define i64 @probe(i64 addrspace(1)* %device, i1 %choose) {
  %a = alloca i64
  store i64 123, i64* %a
  %private = addrspacecast i64* %a to i64 addrspace(8)*
  %other = addrspacecast i64 addrspace(1)* %device to i64 addrspace(8)*
  %p = select i1 %choose, i64 addrspace(8)* %private, i64 addrspace(8)* %other
  %v = load i64, i64 addrspace(8)* %p, align 8
  ret i64 %v
}
''',
    'mixed-phi': '''define i64 @probe(i64 addrspace(1)* %device, i1 %choose) {
  %a = alloca i64
  store i64 123, i64* %a
  %private = addrspacecast i64* %a to i64 addrspace(8)*
  %other = addrspacecast i64 addrspace(1)* %device to i64 addrspace(8)*
  br i1 %choose, label %left, label %right
left:
  br label %merge
right:
  br label %merge
merge:
  %p = phi i64 addrspace(8)* [ %private, %left ], [ %other, %right ]
  %v = load i64, i64 addrspace(8)* %p, align 8
  ret i64 %v
}
''',
    'int-roundtrip': '''define i64 @probe() {
  %a = alloca i64
  %bits = ptrtoint i64* %a to i64
  %p = inttoptr i64 %bits to i64*
  %g = addrspacecast i64* %p to i64 addrspace(8)*
  %v = load i64, i64 addrspace(8)* %g, align 8
  ret i64 %v
}
''',
}

HELPER_TYPE = '%Carrier = type { { i64 addrspace(8)*, i64 }, i32 }\n'
HELPER = '''define internal i64 @read_carrier(%Carrier %carrier) {
  %p = extractvalue %Carrier %carrier, 0, 0
  %v = load volatile i64, i64 addrspace(8)* %p, align 8
  ret i64 %v
}
'''
HELPER_PRIVATE = '''define i64 @probe(i64 %value, i32 %index) {
  %a = alloca i64, align 8
  store i64 %value, i64* %a, align 8
  %g = addrspacecast i64* %a to i64 addrspace(8)*
  %c = insertvalue %Carrier undef, i64 addrspace(8)* %g, 0, 0
  %v = call i64 @read_carrier(%Carrier %c)
  ret i64 %v
}
'''

def check(driver, output):
    output = Path(output); output.mkdir()
    llvm, env = paths()
    fixtures = {name: LAYOUT+HEADER+body+'  ret i64 %v\n}\n'+
                ('!0 = !{!"preserved"}\n' if name=='volatile-metadata' else '')
                for name,body in CASES.items()}
    fixtures.update({name: LAYOUT+body for name,body in CONTROLS.items()})
    fixtures['helper-private'] = LAYOUT+HELPER_TYPE+HELPER+HELPER_PRIVATE
    mixed_helper = '''define i64 @probe(i64 addrspace(1)* %device, i64 %value) {
  %a = alloca i64, align 8
  store i64 %value, i64* %a, align 8
  %g = addrspacecast i64* %a to i64 addrspace(8)*
  %c = insertvalue %Carrier undef, i64 addrspace(8)* %g, 0, 0
  %first = call i64 @read_carrier(%Carrier %c)
  %d = addrspacecast i64 addrspace(1)* %device to i64 addrspace(8)*
  %other = insertvalue %Carrier undef, i64 addrspace(8)* %d, 0, 0
  %second = call i64 @read_carrier(%Carrier %other)
  %v = add i64 %first, %second
  ret i64 %v
}
'''
    fixtures['helper-mixed-callers'] = LAYOUT+HELPER_TYPE+HELPER+mixed_helper
    refused_helpers = {
        'helper-noinline': HELPER.replace('(%Carrier %carrier) {','(%Carrier %carrier) noinline {'),
        'helper-optnone': HELPER.replace('(%Carrier %carrier) {','(%Carrier %carrier) noinline optnone {'),
        'helper-escaping': '@escaped = global i64 (%Carrier)* @read_carrier\n'+HELPER,
        'helper-recursive': HELPER.replace('  ret i64 %v','  %recurse = call i64 @read_carrier(%Carrier %carrier)\n  ret i64 %recurse'),
    }
    for name, helper in refused_helpers.items():
        fixtures[name] = LAYOUT+HELPER_TYPE+helper+HELPER_PRIVATE
    fixtures['helper-instruction-bound'] = LAYOUT+HELPER_TYPE+HELPER.replace(
        '  ret i64 %v', ''.join(f'  %unused{i} = add i64 %v, {i}\n' for i in range(2048))+'  ret i64 %v')+HELPER_PRIVATE
    fixtures['helper-callsite-bound'] = LAYOUT+HELPER_TYPE+HELPER+HELPER_PRIVATE.replace(
        '  ret i64 %v', ''.join(f'  %extra{i} = call i64 @read_carrier(%Carrier %c)\n' for i in range(8))+'  ret i64 %v')
    fixtures['helper-caller-optnone'] = LAYOUT+HELPER_TYPE+HELPER+HELPER_PRIVATE.replace(
        '(i64 %value, i32 %index) {','(i64 %value, i32 %index) noinline optnone {')
    unrelated = ('define internal i64 @unrelated(i64 %x) alwaysinline {\n' +
                 ''.join(f'  %step{i} = add i64 '+('%x' if i==0 else f'%step{i-1}')+f', {i}\n' for i in range(5000)) +
                 '  ret i64 %step4999\n}\n')
    unrelated_caller = HELPER_PRIVATE.replace('  ret i64 %v','  %unused = call i64 @unrelated(i64 %v)\n  ret i64 %v')
    fixtures['helper-unrelated-alwaysinline'] = LAYOUT+HELPER_TYPE+HELPER+unrelated+unrelated_caller
    fixtures['helper-unrelated-call-alwaysinline'] = LAYOUT+HELPER_TYPE+HELPER+unrelated.replace(' alwaysinline {',' {')+unrelated_caller.replace(
        'call i64 @unrelated(i64 %v)','call i64 @unrelated(i64 %v) alwaysinline')
    fixtures['helper-call-noinline'] = LAYOUT+HELPER_TYPE+HELPER+HELPER_PRIVATE.replace(
        'call i64 @read_carrier(%Carrier %c)','call i64 @read_carrier(%Carrier %c) noinline')
    fixtures['helper-device-only'] = LAYOUT+HELPER_TYPE+HELPER+'''define i64 @probe(i64 addrspace(1)* %device) {
  %g = addrspacecast i64 addrspace(1)* %device to i64 addrspace(8)*
  %c = insertvalue %Carrier undef, i64 addrspace(8)* %g, 0, 0
  %v = call i64 @read_carrier(%Carrier %c)
  ret i64 %v
}
'''
    refused = set(CONTROLS) | set(refused_helpers) | {'helper-call-noinline','helper-device-only',
        'helper-instruction-bound','helper-callsite-bound','helper-caller-optnone',
        'helper-unrelated-alwaysinline','helper-unrelated-call-alwaysinline'}
    # A device pointer VALUE loaded out of a private slot must keep its AS1 type.
    fixtures['device-pointee'] = LAYOUT+'''define i64 @probe(i64 addrspace(1)* %device) {
  %slot = alloca i64 addrspace(1)*
  store i64 addrspace(1)* %device, i64 addrspace(1)** %slot
  %generic = addrspacecast i64 addrspace(1)** %slot to i64 addrspace(1)* addrspace(8)*
  %p = load i64 addrspace(1)*, i64 addrspace(1)* addrspace(8)* %generic, align 8
  %v = load i64, i64 addrspace(1)* %p, align 8
  ret i64 %v
}
'''
    rows=[]; comparisons=0
    for name,text in fixtures.items():
        source=output/(name+'.ll'); fixed=output/(name+'-fixed.ll'); source.write_text(text)
        result=subprocess.run([str(driver),str(llvm),str(source),str(fixed)],env=env,capture_output=True,text=True)
        (output/(name+'.stderr')).write_text(result.stderr)
        assert result.returncode==0,(name,result.stderr)
        report=json.loads(result.stdout)
        assert report['valid'] and report['original_unchanged'],(name,report)
        assert report['changed']==(name not in refused),(name,report)
        rewritten=fixed.read_text()
        if name=='volatile-metadata':
            assert re.search(r'load volatile i64, i64\* .*align 4, !compat.tag',rewritten),rewritten
        if name=='atomic':
            assert re.search(r'load atomic i64, i64\* .*monotonic, align 8',rewritten),rewritten
        if name=='device-pointee':
            assert 'load i64 addrspace(1)*, i64 addrspace(1)**' in rewritten,rewritten
            assert 'load i64, i64 addrspace(1)* %p' in rewritten,rewritten
        if name=='helper-mixed-callers':
            assert re.search(r'load volatile i64, i64\*',rewritten),rewritten
            assert re.search(r'load volatile i64, i64 addrspace\(8\)\*',rewritten),rewritten
        if name in CASES or name=='helper-private':
            sources=[]
            for prefix,body in [('original',text),('fixed',rewritten)]:
                p=output/(name+'-'+prefix+'-native.ll')
                p.write_text(body.replace('@probe(', '@'+prefix+'_probe(')); sources.append(str(p))
            binary=output/(name+'.dylib')
            result=subprocess.run(['xcrun','clang','-O2','-Wno-override-module','-dynamiclib',*sources,'-o',str(binary)],capture_output=True,text=True)
            assert result.returncode==0,(name,result.stderr)
            library=ctypes.CDLL(str(binary))
            functions=[library.original_probe,library.fixed_probe]
            for function in functions:
                function.argtypes=[ctypes.c_uint64,ctypes.c_uint32];function.restype=ctypes.c_uint64
            for value in [0,1,(1<<64)-1,1<<63,*[(i*0x9e3779b97f4a7c15)&((1<<64)-1) for i in range(256)]]:
                for index in range(8):
                    assert [f(value,index) for f in functions]==[value,value],(name,value,index)
                    comparisons+=1
        rows.append(dict(name=name,**report))
    report=dict(cases=rows,cpu_comparisons=comparisons)
    (output/'results.json').write_text(json.dumps(report,indent=2)+'\n')
    return report

if __name__=='__main__':
    result=check(Path(sys.argv[1]),Path(sys.argv[2]))
    print(json.dumps(dict(cases=len(result['cases']),cpu_comparisons=result['cpu_comparisons'])))
