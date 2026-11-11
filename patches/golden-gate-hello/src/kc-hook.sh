#!/bin/sh
# golden-gate-hello (kc-hook, after golden-gate-userspace): the first-boot "hello" and Welcome to Mac on the HD 4000 for
# macOS 27, run after the userspace replacement (an oclp-hook's System files are replaced by 27's there) and with
# the build's own hello shaders, rewritten by cache/metallib:
#   SetupAssistantSupportUI default.metallib  <- inputs/hello_metallib_golden_gate (the build's own library rewritten
#                                                by cache/metallib; installed only over the library it was made from)
#   Metal.framework/Versions/A/Metal          <- OCLP's wrapper (kept by golden-gate-userspace), edit-metal.py: the
#                                                hello un-skip + LC_LOAD_WEAK_DYLIB libhelloblur, re-signed ad hoc
#   usr/lib/libhelloblur.dylib (new)          <- inputs/libhelloblur (blur cache + Continue warm-up)
set -eu
S=${1:?System mount}; H=$(cd "$(dirname "$0")" && pwd)
REL=System/Library/PrivateFrameworks/SetupAssistantSupportUI.framework/Versions/A/Resources/default.metallib
LIB=$S/$REL HELLO=$H/inputs/hello_metallib_golden_gate
# SOURCES: the sha256 of the System library the rewrite was made from, then its path.
want=$(awk -v r="$REL" '$2 == r { print $1 }' "$HELLO/SOURCES" 2>/dev/null || true)
have=$(shasum -a 256 "$LIB" | cut -d' ' -f1)
[ -n "$want" ] || { echo "MISSING/unsupported: $HELLO/SOURCES does not name /$REL" >&2; exit 1; }
[ "$have" = "$want" ] || { echo "MISSING/unsupported: /$REL is $have; the hello rewrite was made from $want" >&2; exit 1; }
METAL=$S/System/Library/Frameworks/Metal.framework/Versions/A/Metal
DYLIB=$S/usr/lib/libhelloblur.dylib
W=$(mktemp -d /private/var/tmp/golden-gate-hello.XXXXXX)
"${OCLP_PYTHON:?}" -I "$H/edit-metal.py" "$METAL" "$W/Metal"
codesign -f -s - -i Metal "$W/Metal"
put() { rm -f "$3"; install -o root -g wheel -m $1 "$2" "$3"; chflags restricted "$3"; }
put 755 "$W/Metal" "$METAL"
put 644 "$HELLO/$REL" "$LIB"
put 755 "$H/inputs/libhelloblur" "$DYLIB"
rm -rf "$W"
shasum -a 256 "$LIB" "$METAL" "$DYLIB"
