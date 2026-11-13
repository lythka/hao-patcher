#!/bin/zsh
# Host only: the bundle's adapter path against cache/shaders' replay interposer (fake HD 4000; compile only, no GPU).
# Laptop-faithful: cwd = bundle root, run.sh's sandbox (all writes denied except OUT/NAME), and with a host runtime
# made by `make.py host-runtime --laptop-paths` OCLP's 31001 libLLVM keeps its relative helper path.
# usage: host-replay.sh HOSTRT BUNDLE OUT NAME KERNEL METALLIB [ENV=VALUE...]
#   HOSTRT  make.py host-runtime output; BUNDLE  make.py bundle's metal27-adapter-test folder
#   KERNEL, METALLIB  as for bin/metal-adapter-test (METALLIB relative to BUNDLE/lib)
# Prints one line: NAME exit=N RESULT ...; the log and the stand-in's request dumps are in OUT/NAME.
set -u
H=${1:A}; B=${2:A}; O=$3; name=$4; k=$5; lib=$6; shift 6
out=$O/$name; rm -rf $out; mkdir -p $out/cache $out/dump $out/tmp; out=${out:A}
PROFILE="(version 1)
(allow default)
(deny file-write*)
(allow file-write* (subpath \"$out\") (literal \"/dev/null\") (literal \"/dev/tty\") (regex #\"^/dev/fd/\") (literal \"/dev/dtracehelper\"))"
cd $B
/usr/bin/sandbox-exec -p "$PROFILE" /usr/bin/env -i HOME=$HOME PATH=/usr/bin:/bin TMPDIR=$out/tmp/ \
  DYLD_INSERT_LIBRARIES=$H/bin/ip.dylib:$B/bin/q2log.dylib DYLD_FRAMEWORK_PATH=$B/fw DYLD_LIBRARY_PATH=$H/dl \
  RP_ROOT=$H/root RP_DEVICE_CLASS=MTLIGAccelDevice RP_STEP0=9 RP_SLEEP=0 RP_OUT=$out/dump RP_CACHE_DIR=$out/cache/ \
  RP_PCI_ID=0x01668086 RP_REGISTRY_ID=0x100000a1f RP_MAX_RESOURCE=1610612736 RP_SHARED_MEMORY=1610612736 \
  HAO_ACCEL_PORT=0x4d54 HAO_DEVICE_CLASS=MTLIGAccelDevice HAO_DRIVER_BUNDLE=$H/bundles/AppleIntelHD4000GraphicsMTLDriver.bundle \
  HAO_MTLCOMPILER_3802=$H/fw/MTLCompiler.framework/Versions/3802/MTLCompiler \
  HAO_MTLCOMPILER_31001=$H/fw/MTLCompiler.framework/Versions/31001/MTLCompiler \
  HAO_LLVM_HELPER_31001=$H/fw/GPUCompiler.framework/Versions/31001/Libraries/libLLVM_helper.dylib \
  HAO_EXPECT=adapter HAO_METAL27_INTERNAL=1 MTL_MONOLITHIC_COMPILER=1 HAO_FE_MODULE_CACHE=$out/tmp/metalfe \
  HAO_DOWNGRADE_TEMPLATE=$B/lib/downgrade-template.req7 HAO_STANDIN_DUMP=$out/dump HAO_COMPILE_ONLY=1 HAO_TIMEOUT=120 \
  "$@" $B/bin/metal-adapter-test $k $B/lib/$lib > $out/log.txt 2>&1
rc=$?; print -r -- "$(printf '%-34s exit=%-3s' $name $rc) $(grep -m1 '^RESULT' $out/log.txt)"
