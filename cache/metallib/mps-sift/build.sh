#!/bin/sh
# Build sift_exec for the host's Apple GPU (sift-apple, arm64) and the HD 4000 (sift-intel, x86_64 with the
# replay bridge the private MetalOld compiler route resolves in the executable). Both are ad-hoc signed.
set -eu
OUT=$1
HERE=$(cd "$(dirname "$0")" && pwd)
BRIDGE=$HERE/../../shaders/src/replay.m
mkdir "$OUT"
xcrun clang -O2 -fobjc-arc -Wall -Wno-deprecated-declarations -framework Foundation -framework Metal \
  -framework MetalPerformanceShaders "$HERE/sift_exec.m" -o "$OUT/sift-apple"
xcrun clang -arch x86_64 -mmacosx-version-min=13.0 -O1 -Dmain=unused_replay_main -c "$BRIDGE" -o "$OUT/bridge.o"
xcrun clang -arch x86_64 -mmacosx-version-min=13.0 -O2 -fobjc-arc -Wall -Wno-deprecated-declarations \
  -framework Foundation -framework Metal -framework MetalPerformanceShaders "$HERE/sift_exec.m" "$OUT/bridge.o" \
  -o "$OUT/sift-intel"
codesign -f -s - "$OUT/sift-apple" "$OUT/sift-intel"
xcrun metal -std=metal4.1 -mmacos-version-min=27.0 -c "$HERE/block_probe.metal" -o "$OUT/block_probe.air"
xcrun metallib "$OUT/block_probe.air" -o "$OUT/block_probe.metallib"
shasum -a 256 "$HERE/sift_exec.m" "$HERE/gen.py" "$HERE/block_probe.metal" "$BRIDGE" "$OUT/sift-apple" "$OUT/sift-intel" \
  "$OUT/block_probe.metallib" > "$OUT/build.sha256"
/opt/homebrew/bin/python3.14 -I "$HERE/record.py" "$OUT"
