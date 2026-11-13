#!/bin/zsh
# Host only, the control for host-replay.sh: the same test program and metallib under a cache/shaders replay
# runtime's own MetalOld (in-process compile, 31001 downgrade, 3802), on the same fake HD 4000. MetalOld calls the
# MTLCodeGenService* entry points the host build exports (test.m, HOST_R38_EXPORTS); they go to the bundle's
# stand-in in capture mode (HAO_STANDIN_ROUTE=3802: to OCLP 3802 unchanged, every request and reply dumped), so
# MetalOld's 3802 requests and replies can be compared byte for byte with the adapter's.
# usage: host-control.sh RT BUNDLE R38TEST OUT NAME KERNEL METALLIB [ENV=VALUE...]
#   RT       the replay runtime (cache/shaders/lib/prepare.py); R38TEST  make.py's work/host/metal-adapter-test-r38
set -u
R=${1:A}; B=${2:A}; T=${3:A}; O=$4; name=$5; k=$6; lib=$7; shift 7
out=$O/$name; rm -rf $out; mkdir -p $out/cache $out/dump $out/tmp; out=${out:A}
cd $B
/usr/bin/env -i HOME=$HOME PATH=/usr/bin:/bin TMPDIR=$out/tmp/ \
  DYLD_INSERT_LIBRARIES=$R/bin/ip.dylib:$B/bin/q2log.dylib DYLD_FRAMEWORK_PATH=$R/fw DYLD_LIBRARY_PATH=$R/dl \
  MTL_MONOLITHIC_COMPILER=1 RP_MTLCOMPILER=$B/fw/MTLCompiler.framework/Versions/32023/MTLCompiler \
  HAO_STANDIN_ROUTE=3802 HAO_MTLCOMPILER_3802=$R/fw/MTLCompiler.framework/Versions/3802/MTLCompiler HAO_STANDIN_DUMP=$out/dump \
  RP_ROOT=$R/root RP_DEVICE_CLASS=MTLIGAccelDevice RP_STEP0=9 RP_SLEEP=0 RP_OUT=$out/dump RP_CACHE_DIR=$out/cache/ \
  RP_PCI_ID=0x01668086 RP_REGISTRY_ID=0x100000a1f RP_MAX_RESOURCE=1610612736 RP_SHARED_MEMORY=1610612736 \
  HAO_ACCEL_PORT=0x4d54 HAO_DEVICE_CLASS=MTLIGAccelDevice HAO_DRIVER_BUNDLE=$R/bundles/AppleIntelHD4000GraphicsMTLDriver.bundle \
  HAO_EXPECT=control HAO_COMPILE_ONLY=1 HAO_TIMEOUT=120 "$@" $T $k $B/lib/$lib > $out/log.txt 2>&1
rc=$?; print -r -- "$(printf '%-34s exit=%-3s' $name $rc) $(grep -m1 '^RESULT' $out/log.txt)"
