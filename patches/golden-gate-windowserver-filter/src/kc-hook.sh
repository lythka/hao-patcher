#!/bin/sh
# System writable here, after golden-gate-userspace installed the display shim.
set -eu
S=${1:?System mount}; H=$(cd "$(dirname "$0")" && pwd)
W=$(mktemp -d /private/var/tmp/windowserver-filter.XXXXXX)
WS=$S/usr/lib/dss_shim.dylib
IDENTIFIER=$(codesign -dv "$WS" 2>&1 | sed -n 's/^Identifier=//p')
"${OCLP_PYTHON:?}" -I "$H/load_shim.py" "$WS" "$W/dss_shim.dylib"
codesign -f -s - -i "$IDENTIFIER" --preserve-metadata=entitlements,flags,runtime "$W/dss_shim.dylib"
install -d -m 755 "$S/usr/lib"
chflags noschg,nouchg,norestricted "$WS"
install -o root -g wheel -m 755 "$W/dss_shim.dylib" "$WS"
install -o root -g wheel -m 755 "$H/inputs/windowserver_filter_shim" "$S/usr/lib/windowserver_filter.dylib"
chflags restricted "$WS" "$S/usr/lib/windowserver_filter.dylib"
shasum -a 256 "$WS" "$S/usr/lib/windowserver_filter.dylib"
rm -rf "$W"
