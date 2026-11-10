#!/bin/sh
# make.sh OUT: bootguard for recoveryOS (x86_64), ad-hoc signed. The output name is part of the ad-hoc signature.
set -eu
S=$(cd "$(dirname "$0")" && pwd)/src; O=$1; mkdir -p "$O"
clang -arch x86_64 -mmacosx-version-min=26.0 -O2 -Wall -framework IOKit -framework CoreFoundation -o "$O/bootguard" "$S/bootguard.c"
codesign -s - -f "$O/bootguard"
