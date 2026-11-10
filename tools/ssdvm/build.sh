#!/bin/sh
# build.sh OUTDIR: compile the builder-VM launcher (pipeline/vm/start.sh runs OUTDIR/ssdvm; default store/vm/bin).
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
OUT=${1:?output directory}
mkdir -p "$OUT"
xcrun clang -fobjc-arc -fblocks -O2 -framework Foundation -framework Virtualization "$HERE/ssdvm.m" -o "$OUT/ssdvm"
codesign --force --sign - --entitlements "$HERE/ssdvm.entitlements" "$OUT/ssdvm"
"$OUT/ssdvm" capabilities
