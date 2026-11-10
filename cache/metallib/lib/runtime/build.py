"""Build the separate compiler and GPU-client adapters and their test drivers.

compat.c checks, when it loads, that the plugin and LLVM images are the ones it was written for
(COMPAT_PLUGIN_UUID, COMPAT_LLVM_UUID)."""
import hashlib
import datetime
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

HERE=Path(__file__).resolve().parent

def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def build(output, llvm=None):
    output=Path(output);output.mkdir(parents=True)
    clang=subprocess.check_output(['xcrun','--find','clang'],text=True).strip()
    commands=[]
    if llvm:
        llvm=Path(llvm).resolve()
        shutil.copy2(llvm, output/"libLegacyLLVM.dylib")
    if __package__:
        from .logical32_helpers import helpers
    else:
        from logical32_helpers import helpers
    helper_ir=helpers(False).replace('define internal ', 'define ')+'declare float @air.fast_fmax.f32(float, float) nounwind readnone\n'
    (output/'logical32_helpers.h').write_text('static const char logical32_helper_ir[] =\n'+
        '\n'.join(json.dumps(line+'\n') for line in helper_ir.splitlines())+';\n')
    targets=[
        ('gpu-client.dylib',['gpu_client.c'],['-dynamiclib']),
        ('compat.dylib',['compat.c','trampoline.s'],['-dynamiclib']),
        ('transform-driver',['transform_driver.c','trampoline.s'],[]),
        ('loader-driver',['loader_driver.c','trampoline.s'],[]),
        ('abi-driver',['abi_driver.c','trampoline.s'],[]),
        ('stress-driver',['stress_driver.c','trampoline.s'],[]),
        ('sampler-upload-driver',['sampler_upload_driver.m'],['-framework','Foundation'])]
    if llvm:
        targets.append(('sampler-query.dylib',['sampler_query.c','sampler_query.s'],['-dynamiclib',str(llvm)]))
    for name,files,extra in targets:
        if name=='compat.dylib' and llvm:
            extra=extra+['-DCOMPAT_SAMPLER_QUERY']
        command=['xcrun','clang','-arch','x86_64','-mmacosx-version-min=11.0','-Wall','-Wextra','-Werror','-O2','-I',str(output),*extra,*[str(HERE/n) for n in files],'-o',str(output/name)]
        result=subprocess.run(command,capture_output=True,text=True)
        (output/(name+'.build.log')).write_text(result.stdout+result.stderr)
        commands.append(dict(command=command,exit_code=result.returncode))
        if result.returncode:raise ValueError(result.stderr)
        if name=='sampler-query.dylib':
            dependency='/System/Library/PrivateFrameworks/GPUCompiler.framework/Versions/3802/Libraries/libLLVM.dylib'
            subprocess.run(['install_name_tool','-change',dependency,'@loader_path/libLegacyLLVM.dylib',str(output/name)],check=True)
        # An unsigned x86_64 executable hangs in exec on Apple silicon hosts; sign every build output.
        subprocess.run(['codesign','-f','-s','-',str(output/name)],check=True,capture_output=True)
    unit=HERE.parents[1]
    git=lambda *args: subprocess.check_output(['git','-C',str(unit),*args],text=True).strip()
    revision=int(re.search(r'^## Revisions\n+- r(\d+) ',(unit/'README.md').read_text(),re.M)[1])
    report=dict(unit='cache/metallib',revision=revision,commit=git('rev-parse','HEAD'),
                dirty=bool(git('status','--porcelain','--',str(unit))),
                date=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                compiler=dict(path=clang,sha256=sha(clang)),
                source_sha256={p.name:sha(p) for p in sorted(HERE.iterdir()) if p.is_file()},commands=commands,
                outputs={name:sha(output/name) for name,_,_ in targets})
    report['loading']={
        'compiler':['compat.dylib','sampler-query.dylib (deferred)'],
        'gpu_client':['gpu-client.dylib (cold and cached execution)'],
        'sampler_environment':{'COMPAT_BLOCK_READS':'per-axis-mip0','COMPAT_CLIENT_ABI':'axis-st-v1'},
        'logical32_environment':{'COMPAT_LOGICAL32':'mps-sift-32x1x1'},
        'logical32_dispatch':[32,1,1], 'automatic_service_injection':False}
    if llvm:
        report['legacy_llvm']=dict(path=str(llvm),sha256=sha(llvm))
        report['outputs']['libLegacyLLVM.dylib']=sha(output/'libLegacyLLVM.dylib')
    (output/'build.json').write_text(json.dumps(report,indent=2)+'\n')
    return report

def native_runtime(output,runtime,payload):
    """Prepare the same private compiler used by native controls, from stored inputs."""
    import shutil
    output,runtime,payload=map(lambda p:Path(p).resolve(),(output,runtime,payload))
    output.mkdir(parents=True)
    for part in ('fw','dl','root'):
        shutil.copytree(runtime/part,output/part,symlinks=True)
    shutil.copytree(payload/'System',output/'root/System',dirs_exist_ok=True)
    icbe=payload/'System/Library/Extensions/AppleIntelHD4000GraphicsGLDriver.bundle/Contents/MacOS/iCBE.dylib'
    shutil.copy2(icbe,output/'dl/iCBE.dylib')
    stub=HERE.parents[2]/'shaders/src/IOAccelerator.tbd'
    command=['xcrun','clang','-arch','x86_64','-mmacosx-version-min=13.0','-O2','-dynamiclib',
             str(HERE/'native_paths.c'),str(stub),'-framework','IOKit','-o',str(output/'paths.dylib')]
    subprocess.run(command,check=True,capture_output=True)
    subprocess.run(['codesign','-f','-s','-',str(output/'paths.dylib')],check=True,capture_output=True)
    unit=HERE.parents[1]
    git=lambda *args:subprocess.check_output(['git','-C',str(unit),*args],text=True).strip()
    revision=int(re.search(r'^## Revisions\n+- r(\d+) ',(unit/'README.md').read_text(),re.M)[1])
    files=[HERE/'native_paths.c',Path(__file__),stub]
    files += [p for part in ('fw','dl','root') for p in (runtime/part).rglob('*') if p.is_file()]
    files += [p for p in (payload/'System').rglob('*') if p.is_file()]
    report=dict(unit='cache/metallib',revision=revision,commit=git('rev-parse','HEAD'),
                dirty=bool(git('status','--porcelain','--',str(unit))),
                date=datetime.datetime.now(datetime.timezone.utc).isoformat(),command=command,
                inputs={str(p):sha(p) for p in files},
                outputs={str(p.relative_to(output)):sha(p) for p in output.rglob('*') if p.is_file()})
    (output/'build.json').write_text(json.dumps(report,indent=2)+'\n')
    return report

if __name__=='__main__':
    print(json.dumps(build(sys.argv[1])['outputs'],indent=2))
