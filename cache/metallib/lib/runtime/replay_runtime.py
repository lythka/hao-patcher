"""The 3802 compiler the runtime layer runs against: a replay runtime prepared by cache/shaders (lib/prepare.py,
the `runtime` folder of a `cache/shaders/make` work directory), named by METALLIB_RUNTIME."""
import os
from pathlib import Path


def paths():
    root = Path(os.environ['METALLIB_RUNTIME'])
    llvm = root / 'fw/GPUCompiler.framework/Versions/3802/Libraries/libLLVM.dylib'
    env = {k: v for k, v in os.environ.items() if not k.startswith(('DYLD_', 'RP_', 'COMPAT_'))}
    env.update(DYLD_FRAMEWORK_PATH=str(root / 'fw'), DYLD_LIBRARY_PATH=str(root / 'dl'))
    return llvm, env
