#!/bin/sh
# make.sh OUT: lcwrap + lcfast.dylib for recoveryOS (x86_64), ad-hoc signed (amfi=0x80 in our recovery).
set -eu
S=$(cd "$(dirname "$0")" && pwd)/src; O=$1; mkdir -p "$O"
clang -arch x86_64 -mmacosx-version-min=26.0 -O2 -Wall -o "$O/lcwrap" "$S/lcwrap.c"
clang -arch x86_64 -mmacosx-version-min=26.0 -O2 -Wall -fobjc-arc -dynamiclib -framework AppKit -o "$O/lcfast.dylib" "$S/lcfast.m"
codesign -s - -f "$O/lcwrap" "$O/lcfast.dylib"
