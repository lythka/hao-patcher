#!/bin/sh
# After Golden Gate userspace: attach the shared operation to the legacy driver.
set -eu
S=${1:?System mount}; H=$(cd "$(dirname "$0")" && pwd)
DRIVER=$S/System/Library/Extensions/AppleIntelHD4000GraphicsMTLDriver.bundle/Contents/MacOS/AppleIntelHD4000GraphicsMTLDriver
W=$(mktemp -d /private/var/tmp/metal-framebuffer.XXXXXX)
"${OCLP_PYTHON:?}" -I "$H/add_weak_dylib.py" "$DRIVER" "$W/driver" /usr/lib/libmetal-framebuffer.dylib
codesign -f -s - -i AppleIntelHD4000GraphicsMTLDriver "$W/driver"
install -d -m 755 "$S/usr/lib"
chflags noschg,nouchg,norestricted "$DRIVER"
install -o root -g wheel -m 755 "$W/driver" "$DRIVER"
install -o root -g wheel -m 755 "$H/inputs/metal_framebuffer/libmetal-framebuffer.dylib" "$S/usr/lib/libmetal-framebuffer.dylib"
chflags restricted "$DRIVER" "$S/usr/lib/libmetal-framebuffer.dylib"
shasum -a 256 "$DRIVER" "$S/usr/lib/libmetal-framebuffer.dylib"
rm -rf "$W"
