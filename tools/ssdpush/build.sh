#!/bin/zsh
# Build ssdpush-agent for the target Mac's macOS Recovery (x86_64, SSSE3 BLAKE2b) and for this Mac (local tests).
# usage: build.sh [zstd-1.5.x source dir]   (default: store/cache/zstd/zstd-1.5.7). Outputs stay untracked.
set -e
H=${0:A:h}; Z=${1:-${H:h:h}/store/cache/zstd/zstd-1.5.7}
SRC=($H/agent.c $Z/lib/common/{entropy_common,error_private,fse_decompress,xxhash,zstd_common,debug}.c
     $Z/lib/decompress/{huf_decompress,zstd_ddict,zstd_decompress,zstd_decompress_block}.c)
FLAGS=(-O2 -I$H -I$Z/lib -I$Z/lib/common -DZSTD_DISABLE_ASM -DZSTD_MULTITHREAD=0 -pthread)
clang -arch x86_64 -mmacosx-version-min=26.0 $FLAGS -mssse3 $SRC $H/blake2b_ssse3.c -framework CoreFoundation -framework IOKit -o $H/ssdpush-agent-macos-x86_64
clang $FLAGS $SRC $H/blake2b.c -framework CoreFoundation -framework IOKit -o $H/ssdpush-agent-macos
ls -la $H/ssdpush-agent-*
