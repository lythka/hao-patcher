#!/bin/sh
# make.sh OUTDIR: build libhelloblur.dylib (row libhelloblur) from src/hello_blurcache.m on the host, as the user.
#   x86_64, macOS 13 minimum, MacOSX26 SDK, MRC (-fno-objc-arc), -O2, install name /usr/lib/libhelloblur.dylib,
#   links only Foundation, libobjc and libSystem; ad hoc signed with identifier local.libhelloblur.
# The bytes depend on the toolchain: the linker writes its own version into LC_BUILD_VERSION, and the UUID and the
# signature's first page hash follow from it. compare.py tells such a rebuild from a real difference.
# HELLOBLUR_SDK overrides the SDK (the row was made with the Command Line Tools' MacOSX26.sdk, 26.5).
set -eu
H=$(cd "$(dirname "$0")" && pwd); OUT=${1:?output dir}
SDK=${HELLOBLUR_SDK:-/Library/Developer/CommandLineTools/SDKs/MacOSX26.sdk}
[ -d "$SDK" ] || { echo "make.sh: no SDK at $SDK (set HELLOBLUR_SDK)" >&2; exit 1; }
mkdir -p "$OUT"
clang -arch x86_64 -mmacosx-version-min=13.0 -isysroot "$SDK" -dynamiclib -fno-objc-arc -O2 -Wall -Werror \
    -Wl,-no_warn_inits -install_name /usr/lib/libhelloblur.dylib -framework Foundation -lobjc \
    -o "$OUT/libhelloblur.dylib" "$H/src/hello_blurcache.m"
codesign -f -s - -i local.libhelloblur "$OUT/libhelloblur.dylib"
shasum -a 256 "$OUT/libhelloblur.dylib"
