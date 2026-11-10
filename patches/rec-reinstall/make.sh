#!/bin/sh
# make.sh OUT: Reinstall macOS's binaries for the Recovery (x86_64): InstallAssistant (src/Reinstall.m, ad hoc
# signed), ssdinstall (tools/ssdpush/ssdinstall.c, ad hoc signed) and ssdpush-agent (tools/ssdpush/build.sh, zstd
# source from store/cache/zstd or $ZSTD). Each output is built at a path with a folder: a bare output name changes
# the binary's LC_UUID.
set -eu
H=$(cd "$(dirname "$0")" && pwd); TOP=${H%/*/*}; O=$1; mkdir -p "$O"
X86="-arch x86_64 -mmacosx-version-min=26.0 -O2 -Wall"
clang $X86 -fobjc-arc -framework AppKit -framework IOKit -o "$O/InstallAssistant" "$H/src/Reinstall.m"
clang $X86 -framework CoreFoundation -framework IOKit -o "$O/ssdinstall" "$TOP/tools/ssdpush/ssdinstall.c"
codesign -s - -f "$O/InstallAssistant" "$O/ssdinstall"
zsh "$TOP/tools/ssdpush/build.sh" ${ZSTD:+"$ZSTD"} >/dev/null
cp "$TOP/tools/ssdpush/ssdpush-agent-macos-x86_64" "$O/ssdpush-agent"
