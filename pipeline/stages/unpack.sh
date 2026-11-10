#!/bin/zsh
# unpack.sh INSTALLER OUT [REFLOCK]: the per-build the pristine stage inputs from a macOS InstallAssistant.pkg, on the host as the user.
#   OUT/unpack   installer unpacked (payload.yaa, fixup.yaa, zip/, out/ cryptexes, tree/ = System tree)
#   OUT/lists    sys.tsv data.tsv data-symlinks.tsv (Apple's final owners/modes/flags/xattrs/mtimes for hmeta)
#   OUT/preboot-recovery-map.tsv   Preboot/Recovery files of a real install -> this build's unpack outputs, rebased
#                from REFLOCK's map (default pipeline/inputs/25G241, made from an installed 25G241) by BuildManifest roles
# Writes the inputs-lock rows for the new build to pipeline/inputs/BUILD/ (one file per row).
set -eu
PKG=${1:?InstallAssistant.pkg}; OUT=${2:?output folder}; TOP=${0:A:h:h:h}; REFLOCK=${3:-$TOP/pipeline/inputs/25G241}
U=$TOP/pipeline/unpack; L=$TOP/pipeline/lock.py
mkdir -p $OUT
[[ -d $OUT/unpack ]] || python3 -I $U/unpack-installer --keep-intermediates $PKG $OUT/unpack
[[ -f $OUT/lists/sys.tsv ]] || HYB_TREE=$OUT/unpack/tree python3 -I $U/pristine-tools/mklist.py $OUT/unpack $OUT/lists
BUILD=$(/usr/libexec/PlistBuddy -c 'Print :ProductBuildVersion' $OUT/unpack/zip/AssetData/boot/BuildManifest.plist)
python3 -I $TOP/pipeline/stages/stage-pb.py --rebase-map $(python3 -I $L path $REFLOCK preboot_map) \
    $(python3 -I $L path $REFLOCK unpack)/zip/AssetData/boot/BuildManifest.plist $OUT/unpack $OUT/unpack/tree $BUILD \
    > $OUT/preboot-recovery-map.tsv
D=$TOP/pipeline/inputs/$BUILD; mkdir -p $D   # one file per lock row; the kdk row is added by hand
row() { local p=${3/#$TOP\//}; print -r -- "$2  ${p/#$HOME/~}" > $D/$1; }   # paths under the checkout as store/...
row installer   $(shasum -a 256 $PKG | cut -c1-64) $PKG
row unpack      - $OUT/unpack
row tree        - $OUT/unpack/tree
row lists       - $OUT/lists
row preboot_map $(shasum -a 256 $OUT/preboot-recovery-map.tsv | cut -c1-64) $OUT/preboot-recovery-map.tsv
echo "unpack.sh: lock rows written to $D (add kdk)"
