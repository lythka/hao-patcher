#!/bin/sh
# Apple-GPU reference on the host: inputs, MPSImageSIFT through MPS (with pyramid dumps), then direct
# extraction-kernel dispatches on those pyramids with Apple's original 27.0 library and the host's own.
#   reference.sh BIN WORK ORIGINAL_27_0_MPSIMAGE_METALLIB [SIZES [FORMATS]]
# Writes WORK/inputs (copied to the Intel target), WORK/apple-api, WORK/apple-kernel-27.0, WORK/apple-kernel-host.
set -eu
BIN=$1 WORK=$2 LIB=$3
SIZES=${4:-16x16,17x23,31x33,64x64,97x61,128x128,255x129,256x256,321x200,640x480,1000x700}
FORMATS=${5:-r32float,r16float,r16unorm,r8unorm}
HERE=$(cd "$(dirname "$0")" && pwd)
PY=/opt/homebrew/bin/python3.14
$PY -I "$HERE/gen.py" sources "$WORK/inputs" --sizes "$SIZES" --formats "$FORMATS" --dump-pyramids
"$BIN/sift-apple" api "$WORK/apple-api" "$WORK/inputs/api-cases.json" 2> "$WORK/apple-api.log"
$PY -I "$HERE/gen.py" kernel-cases "$WORK/inputs" "$WORK/apple-api"
"$BIN/sift-apple" kernel "$WORK/apple-kernel-27.0" "$WORK/inputs/kernel-cases.json" "$LIB" 2> "$WORK/apple-kernel-27.0.log"
"$BIN/sift-apple" kernel "$WORK/apple-kernel-host" "$WORK/inputs/kernel-cases.json" \
  /System/Library/Frameworks/MetalPerformanceShaders.framework/Versions/A/Frameworks/MPSImage.framework/Versions/A/Resources/default.metallib \
  2> "$WORK/apple-kernel-host.log"
