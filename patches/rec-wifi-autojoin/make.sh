#!/bin/sh
# make.sh OUT: wifijoin.dylib for recoveryOS (x86_64), ad-hoc signed (our recovery boots with amfi=0x80).
set -eu
S=$(cd "$(dirname "$0")" && pwd)/src; O=$1; mkdir -p "$O"
clang -arch x86_64 -mmacosx-version-min=26.0 -O2 -Wall -fobjc-arc -dynamiclib -framework Foundation \
    -o "$O/wifijoin.dylib" "$S/wifijoin.m"
codesign -s - -f "$O/wifijoin.dylib"
