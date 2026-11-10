#!/bin/sh
# metalold-selectors (oclp-hook): the macOS 26 Metal selectors that OCLP's MetalOld stack does not answer, added by a
# dylib the HD 4000 Metal driver bundle loads. Runs after OCLP's Metal 3802 patch set put the driver bundle in place.
#   usr/lib/libmetalold-selectors.dylib (new)                       <- inputs/metalold_selectors (build/)
#   System/Library/Extensions/AppleIntelHD4000GraphicsMTLDriver.bundle/Contents/MacOS/AppleIntelHD4000GraphicsMTLDriver
#       + one LC_LOAD_WEAK_DYLIB for the dylib in the zero padding after its load commands, re-signed ad hoc
# Both files are ad hoc signed, so the image's OpenCore boot-args must relax AMFI (see hao-opencore).
#   hook.sh SYSTEM_MOUNT DATA_MOUNT     env OCLP_PYTHON
set -eu
S=${1:?System mount}; H=$(cd "$(dirname "$0")" && pwd)
DRIVER=$S/System/Library/Extensions/AppleIntelHD4000GraphicsMTLDriver.bundle/Contents/MacOS/AppleIntelHD4000GraphicsMTLDriver
DYLIB=$S/usr/lib/libmetalold-selectors.dylib
W=$(mktemp -d /private/var/tmp/metalold-selectors.XXXXXX)

"${OCLP_PYTHON:?}" -I "$H/add_weak_dylib.py" "$DRIVER" "$W/driver" /usr/lib/libmetalold-selectors.dylib
codesign -f -s - -i AppleIntelHD4000GraphicsMTLDriver "$W/driver"
put() { rm -f "$3"; install -o root -g wheel -m $1 "$2" "$3"; chflags restricted "$3"; }   # put MODE SRC DEST, as Apple's
put 755 "$H/inputs/metalold_selectors/libmetalold-selectors.dylib" "$DYLIB"
put 755 "$W/driver" "$DRIVER"
rm -rf "$W"
shasum -a 256 "$DYLIB" "$DRIVER"
