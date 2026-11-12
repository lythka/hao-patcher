#!/bin/sh
# golden-gate-metal-argbuf (kc-hook): native Metal argument buffers and ASTC textures for the HD 4000
# (tools/metal-argbuf). Runs in set order after golden-gate-metal-libraries (which installs the repaired iCBE the
# wrapper re-exports) and golden-gate-metal-framebuffer (the previous weak dylib in the same driver binary).
#   AppleIntelHD4000GraphicsGLDriver.bundle/Contents/MacOS/iCBE-real.dylib  <- the installed iCBE, moved
#   AppleIntelHD4000GraphicsGLDriver.bundle/Contents/MacOS/iCBE.dylib       <- inputs/metal_argbuf/iCBE.dylib (wrapper)
#   AppleIntelHD4000GraphicsMTLDriver.bundle/Contents/MacOS/libargbuf.dylib <- inputs/metal_argbuf/libargbuf.dylib
#   AppleIntelHD4000GraphicsMTLDriver.bundle/Contents/MacOS/AppleIntelHD4000GraphicsMTLDriver
#       + one LC_LOAD_WEAK_DYLIB for libargbuf.dylib in the zero padding after its load commands, re-signed ad hoc
# Refuses unless the installed iCBE is byte-identical to the one the wrapper was linked against
# (inputs/metal_argbuf/iCBE-real.dylib): the wrapper's re-export list comes from that file.
#   kc-hook.sh SYSTEM_MOUNT     env OCLP_PYTHON
set -eu
S=${1:?System mount}; H=$(cd "$(dirname "$0")" && pwd); IN=$H/inputs/metal_argbuf
GL=/System/Library/Extensions/AppleIntelHD4000GraphicsGLDriver.bundle/Contents/MacOS
MTL=/System/Library/Extensions/AppleIntelHD4000GraphicsMTLDriver.bundle/Contents/MacOS
DRIVER=$S$MTL/AppleIntelHD4000GraphicsMTLDriver

[ -e "$S$GL/iCBE-real.dylib" ] && { echo "golden-gate-metal-argbuf: $GL/iCBE-real.dylib already exists" >&2; exit 1; }
cmp -s "$S$GL/iCBE.dylib" "$IN/iCBE-real.dylib" || {
    echo "golden-gate-metal-argbuf: $GL/iCBE.dylib is not the iCBE the wrapper was built against" >&2; exit 1; }

W=$(mktemp -d /private/var/tmp/metal-argbuf.XXXXXX)
"${OCLP_PYTHON:?}" -I "$H/add_weak_dylib.py" "$DRIVER" "$W/driver" "$MTL/libargbuf.dylib"
codesign -f -s - -i AppleIntelHD4000GraphicsMTLDriver "$W/driver"
put() { rm -f "$3"; install -o root -g wheel -m $1 "$2" "$3"; chflags restricted "$3"; }   # put MODE SRC DEST, as Apple's
put 755 "$IN/iCBE-real.dylib" "$S$GL/iCBE-real.dylib"
put 755 "$IN/iCBE.dylib" "$S$GL/iCBE.dylib"
put 755 "$IN/libargbuf.dylib" "$S$MTL/libargbuf.dylib"
chflags noschg,nouchg,norestricted "$DRIVER"
put 755 "$W/driver" "$DRIVER"
rm -rf "$W"
shasum -a 256 "$S$GL/iCBE.dylib" "$S$GL/iCBE-real.dylib" "$S$MTL/libargbuf.dylib" "$DRIVER"
