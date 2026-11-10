#!/bin/sh
# Run after golden-gate-userspace, which replaces OCLP's System libraries.
set -eu
SYS=$1
IN="$(dirname "$0")/inputs/metallib_repairs_golden_gate"
find "$IN/System" -type f -name '*.metallib' | while IFS= read -r source; do
    relative=${source#"$IN/"}
    install -d -m 755 "$(dirname "$SYS/$relative")"
    install -o 0 -g 0 -m 644 "$source" "$SYS/$relative"
    echo "golden-gate-metal-libraries: installed /$relative"
done
RUNTIME="$(dirname "$0")/inputs/metal_quad_runtime_golden_gate"
relative=System/Library/Extensions/AppleIntelGraphicsShared.bundle/Contents/Resources/runtime.igil64.split
install -d -m 755 "$(dirname "$SYS/$relative")"
install -o 0 -g 0 -m 644 "$RUNTIME/$relative" "$SYS/$relative"
echo "golden-gate-metal-libraries: installed /$relative"
relative=System/Library/Extensions/AppleIntelHD4000GraphicsGLDriver.bundle/Contents/MacOS/iCBE.dylib
install -d -m 755 "$(dirname "$SYS/$relative")"
install -o 0 -g 0 -m 755 "$RUNTIME/$relative" "$SYS/$relative"
echo "golden-gate-metal-libraries: installed /$relative"
