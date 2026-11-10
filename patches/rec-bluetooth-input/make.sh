#!/bin/sh
# make.sh OUT: btpower for recoveryOS (x86_64), ad-hoc signed with its Bluetooth entitlements (our recovery boots with
# amfi=0x80). Run on the host by pipeline/recovery.
set -eu
S=$(cd "$(dirname "$0")" && pwd)/src; O=$1; mkdir -p "$O"
clang -arch x86_64 -mmacosx-version-min=26.0 -O2 -Wall -framework IOBluetooth -o "$O/btpower" "$S/btpower.c"
codesign -s - -f --entitlements "$S/bt.entitlements" "$O/btpower"
