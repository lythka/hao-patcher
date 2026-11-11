#!/bin/sh
# golden-gate-native-identities (kc-hook; last oclp-hook of the set, after every recipe that re-signs a program):
# installs the Apple-signed originals of the native programs the image re-signed ad hoc under /usr/lib/hao/originals
# and lists them in /usr/lib/hao/native-identities for the code identity carrier (golden-gate-userspace).
#   kc-hook.sh SYSTEM_MOUNT     env OCLP_PYTHON, and from golden-image.py:
#     TAHOE_NATIVE_ARTIFACT     the assembly's tahoe native donors artifact (payload/system is a source root)
# Source roots, in order: the build's pristine 27.x System (golden_gate_userspace's filesystem.dmg), Tahoe's donors.
set -eu
S=${1:?System mount}; H=$(cd "$(dirname "$0")" && pwd)
NATIVE=${TAHOE_NATIVE_ARTIFACT:?set by golden-image.py}/payload/system
[ -d "$NATIVE" ] || { echo "native-identities: no $NATIVE" >&2; exit 1; }
W=$(mktemp -d /private/var/tmp/native-identities.XXXXXX)
mkdir "$W/fs"
DEV=$(hdiutil attach -readonly -nobrowse -owners on -mountpoint "$W/fs" "$H/inputs/golden_gate_userspace/filesystem.dmg" \
      | awk 'NR==1 {print $1}')
trap 'hdiutil detach "$DEV" >/dev/null 2>&1 || true; rmdir "$W/fs" "$W" 2>/dev/null || true' EXIT
"${OCLP_PYTHON:?}" -I "$H/native_identities.py" install "$S" --source "$W/fs" --source "$NATIVE"
