#!/bin/sh
# Both native Spotlight query daemons need the missing CoreSpotlight getter.
set -eu
S=${1:?System mount}; H=$(cd "$(dirname "$0")" && pwd)
SHIM=${2:-$H/inputs/corespotlight_compat_shim}
PYTHON=${3:-${OCLP_PYTHON:-python3}}
SUPPORT=$S/System/Library/Frameworks/CoreServices.framework/Versions/A/Frameworks/Metadata.framework/Versions/A/Support
W=$(mktemp -d /private/var/tmp/corespotlight-compat.XXXXXX)
trap 'rm -rf "$W"' EXIT
install -d -m 755 "$S/usr/lib"
for NAME in corespotlightd managedcorespotlightd; do
    BIN=$SUPPORT/$NAME
    IDENTIFIER=$(codesign -dv "$BIN" 2>&1 | sed -n 's/^Identifier=//p')
    "$PYTHON" -I "$H/add_load_dylib.py" "$BIN" "$W/$NAME" /usr/lib/corespotlight_compat.dylib
    codesign -f -s - -i "$IDENTIFIER" --preserve-metadata=entitlements,flags,runtime "$W/$NAME"
    chflags noschg,nouchg,norestricted "$BIN"
    install -o root -g wheel -m 755 "$W/$NAME" "$BIN"
    chflags restricted "$BIN"
    shasum -a 256 "$BIN"
done
install -o root -g wheel -m 755 "$SHIM" "$S/usr/lib/corespotlight_compat.dylib"
chflags restricted "$S/usr/lib/corespotlight_compat.dylib"
shasum -a 256 "$S/usr/lib/corespotlight_compat.dylib"
rm -rf "$W"
