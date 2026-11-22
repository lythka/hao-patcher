#!/bin/sh
set -eu
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
TOP=$(CDPATH= cd -- "$HERE/../.." && pwd)
OUT=${1:?output}
sh "$HERE/../golden-gate-shared-cache-catalog/make.sh" "$OUT"
mkdir -p "$OUT/cache"
for module in cachebuild console safety; do cp "$TOP/tools/hao/src/$module.py" "$OUT/cache/"; done
cp "$TOP/pipeline/cache-rebuild/26B131.json" "$OUT/cache/config.json"
cp "$TOP/pipeline/cache-rebuild/26B131-inputs.json" "$OUT/cache/inputs.json"
for row in shared_cache_builder swift_metadata_catalog; do
    cp "$TOP/pipeline/inputs/common/$row" "$OUT/cache/$row.pin"
done
