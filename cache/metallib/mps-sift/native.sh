#!/bin/sh
# HD 4000 run (on the Intel target, as root, in a private folder): in-process compiler, private 3802 runtime,
# per-axis block translation, a fresh private shader cache per mode. Each mode runs twice: cold (empty
# cache: compile) and warm (a second process on the populated cache).
#   [MODES="api kernel block baseline width8 logical32"] native.sh COMPILER_ROOT TRANSLATOR_DIR WORK
# WORK holds sift-intel, block_probe.metallib and inputs/ (from reference.sh); results go to
# WORK/intel-MODE-{cold,warm}, logs to WORK/intel-MODE-PHASE.log, wall times to WORK/phases.txt.
# api runs only fused cases: MPS's unfused ExtractFeaturePoints stalls the HD 4000 GPU for ~6 s and fails.
set -eu
C=$1 T=$2 WORK=$3
LIB=${MPS_LIBRARY:-/System/Library/Frameworks/MetalPerformanceShaders.framework/Versions/A/Frameworks/MPSImage.framework/Versions/A/Resources/default.metallib}
cd "$WORK"
sysctl -n kern.bootsessionuuid >> boot-before.txt
shasum -a 256 sift-intel block_probe.metallib "$LIB" "$T/compat.dylib" "$T/gpu-client.dylib" "$T/sampler-query.dylib" "$C/paths.dylib" \
  "$C/dl/"*.dylib "$C/fw/Metal.framework/Versions/A/Metal" /System/Library/Frameworks/Metal.framework/Versions/A/Metal >> hashes.txt
run() {
  env DYLD_FRAMEWORK_PATH="$C/fw" DYLD_LIBRARY_PATH="$C/dl" DYLD_INSERT_LIBRARIES="$C/paths.dylib:$T/gpu-client.dylib:$T/compat.dylib" \
    RP_MTLCOMPILER="$C/fw/MTLCompiler.framework/Versions/3802/MTLCompiler" RP_CACHE_DIR="$WORK/$1/" RP_ROOT="$C/root" \
    MTL_MONOLITHIC_COMPILER=1 COMPAT_CLIENT_ABI=axis-st-v1 COMPAT_BLOCK_READS=per-axis-mip0 \
    COMPAT_LOGICAL32="${6:-}" RP_SLEEP=0 "$WORK/sift-intel" "$2" "$WORK/$3" "$4" ${5:+"$5"}
}
for mode in ${MODES:-api kernel}; do
  mkdir "cache-$mode"
  for phase in cold warm; do
    start=$(date +%s); rc=0; o=intel-$mode-$phase
    case $mode in
      api) run cache-api api $o inputs/api-fused-cases.json > $o.stdout 2> $o.log || rc=$? ;;
      kernel) run cache-kernel kernel $o inputs/kernel-cases.json "$LIB" > $o.stdout 2> $o.log || rc=$? ;;
      baseline) run cache-baseline kernel $o inputs/baseline-cases.json "$LIB" > $o.stdout 2> $o.log || rc=$? ;;
      logical32) run cache-logical32 kernel $o inputs/baseline-cases.json "$LIB" mps-sift-32x1x1 > $o.stdout 2> $o.log || rc=$? ;;
      width8) run cache-width8 kernel $o inputs/width8-cases.json "$WORK/width8.metallib" > $o.stdout 2> $o.log || rc=$? ;;
      block) run cache-block block $o inputs/block-cases.json "$WORK/block_probe.metallib" > $o.stdout 2> $o.log || rc=$? ;;
      *) echo "unknown mode: $mode" >&2; exit 2 ;;
    esac
    echo "$mode $phase rc=$rc wall_s=$(( $(date +%s) - start ))" | tee -a phases.txt
    if [ "$rc" -ne 0 ]; then exit "$rc"; fi
  done
done
sysctl -n kern.bootsessionuuid >> boot-after.txt
