#!/bin/sh
# golden-gate-dyld-internal (kc-hook, in set order after hao-runtime, which installs its replace/dyld): only dyld counts
# as an internal install. dyld4::SyscallDelegate::internalInstall() answers true (src/edit_dyld.py, edits/dyld.json),
# so dyld keeps loading OCLP's on-disk Metal roots without the ic_csr_internal boot-arg (hao-opencore r52 drops it and
# refuses an image without this edit). Re-signed ad hoc with hao-runtime's identifier, mode/owner/flags as before.
set -eu
S=${1:?System mount}; H=$(cd "$(dirname "$0")" && pwd)
D=$S/usr/lib/dyld
W=$(mktemp -d /private/var/tmp/golden-gate-dyld-internal.XXXXXX)
"${OCLP_PYTHON:?}" -I "$H/edit_dyld.py" "$D" "$W/dyld"
codesign -f -s - -i com.apple.darwin.ignition "$W/dyld"
chflags norestricted "$D" 2>/dev/null || true
rm -f "$D"; install -o root -g wheel -m 755 "$W/dyld" "$D"; chflags restricted "$D"
rm -rf "$W"
shasum -a 256 "$D"
