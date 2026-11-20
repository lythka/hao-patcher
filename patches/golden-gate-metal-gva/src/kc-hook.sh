#!/bin/sh
# golden-gate-metal-gva (kc-hook, after the OCLP hardware overlay): OCLP's AppleGVA (Big Sur GVA) with Intel hardware
# JPEG decode reported as unavailable (edit-gva.py), re-signed ad hoc. On the HD 4000, GVA's JPEG decode hands
# Photos (and every ImageIO client) surfaces before they are complete: a stale strip during Photos' zoom.
# Unfolded images only: the shared-cache fold checks AppleGVA's bytes against its artifact and refuses this one.
#   kc-hook.sh SYSTEM_MOUNT     env OCLP_PYTHON
set -eu
S=${1:?System mount}; H=$(cd "$(dirname "$0")" && pwd)
GVA=$S/System/Library/PrivateFrameworks/AppleGVA.framework/Versions/A/AppleGVA
W=$(mktemp -d /private/var/tmp/golden-gate-metal-gva.XXXXXX)
"${OCLP_PYTHON:?}" -I "$H/edit-gva.py" "$GVA" "$W/AppleGVA"
codesign -f -s - --preserve-metadata=identifier,entitlements "$W/AppleGVA"
chflags noschg,nouchg,norestricted "$GVA"
rm -f "$GVA"; install -o root -g wheel -m 755 "$W/AppleGVA" "$GVA"; chflags restricted "$GVA"
rm -rf "$W"
shasum -a 256 "$GVA"
