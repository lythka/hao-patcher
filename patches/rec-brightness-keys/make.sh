#!/bin/sh
# make.sh OUT: brightkeys for recoveryOS (x86_64), ad-hoc signed with the HID-monitor entitlement (amfi=0x80).
set -eu
S=$(cd "$(dirname "$0")" && pwd)/src; O=$1; mkdir -p "$O"
clang -arch x86_64 -mmacosx-version-min=26.0 -fobjc-arc -O2 -Wall -F/System/Library/PrivateFrameworks -framework AppKit \
    -framework DisplayServices -framework IOKit -framework CoreAudio -framework QuartzCore -o "$O/brightkeys" "$S/brightkeys.m"
codesign -s - -f --entitlements "$S/brightkeys.entitlements" "$O/brightkeys"
