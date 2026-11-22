# LLVM tools used by the installer builder

LLVM 15.0.7 is downloaded from the llvm-project llvmorg-15.0.7 release and built
from source by `make`. License: Apache-2.0 WITH LLVM-exception. The source archive
is pinned in the producer; its `llvm/LICENSE.TXT` accompanies the binaries.
The legacy assembler is hao's own source in `cache/metallib/shader-runtime-helpers`.
