#!/bin/sh
# Golden Gate native dyld still probes Rosetta's cache directory. This image
# merges Rosetta caches into OS; expose the same files at the expected path.
set -eu
PREBOOT=${1:?Preboot mount required}
LIB="$PREBOOT/Cryptexes/Rosetta/System/Library"
ALIAS=../../../OS/System/Library/dyld
if [ -L "$LIB/dyld" ]; then
    [ "$(readlink "$LIB/dyld")" = "$ALIAS" ] || {
        echo "Unexpected Rosetta cache alias: $LIB/dyld" >&2; exit 1;
    }
    exit 0
fi
[ ! -e "$LIB/dyld" ] || {
    echo "Rosetta cache directory already populated: $LIB/dyld" >&2; exit 1;
}
for DIR in "$PREBOOT/Cryptexes" "$PREBOOT/Cryptexes/Rosetta" \
    "$PREBOOT/Cryptexes/Rosetta/System" "$LIB"; do
    [ -d "$DIR" ] || install -d -m 755 "$DIR"
done
ln -s "$ALIAS" "$LIB/dyld"
