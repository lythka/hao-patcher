#!/bin/zsh
# test.sh WORK [-k]: host test of the Reinstall path without a laptop, as the user (no sudo), in a new folder WORK:
# a small GPT + APFS disk image with a random file -> ssdimg repository -> `ssdimg bundle` (2 MiB segments, so the
# bundle has several stream files) -> bundle-check -> this Mac's builds of ssdinstall and ssdpush-agent install it
# into a larger file (backup GPT relocated, verify pass), again with --delta -> the file's volume holds the same file.
# Also builds make.sh's x86_64 Recovery binaries. -k keeps WORK/bundle (a test bundle for src/recovery-check.sh).
set -eu
W=${1:?work folder (new)}; KEEP=${2:-}
H=${0:A:h}; TOP=${H:h:h}; SSDIMG=$TOP/tools/bin/ssdimg; ZSTD=${ZSTD:-$TOP/store/cache/zstd/zstd-1.5.7}
[[ ! -e $W ]] || { echo "$W exists"; exit 2; }
mkdir -p $W/mnt; D=""
trap '[[ -z $D ]] || hdiutil detach -force $D >/dev/null 2>&1; [[ $KEEP == -k ]] || rm -rf $W/repo $W/*.img' EXIT
ok() { echo "ok   $*"; }

zsh $TOP/tools/ssdpush/build.sh $ZSTD >/dev/null
clang -O2 -Wall -framework CoreFoundation -framework IOKit -o $W/ssdinstall $TOP/tools/ssdpush/ssdinstall.c
A=$TOP/tools/ssdpush/ssdpush-agent-macos
ZSTD=$ZSTD sh $H/make.sh $W/build >/dev/null && file $W/build/* | grep -c x86_64 | grep -qx 3 && ok "make.sh: 3 x86_64 binaries"

mkfile -n 300m $W/src.img
D=$(hdiutil attach -imagekey diskimage-class=CRawDiskImage -nomount $W/src.img | awk '$1 ~ /^\/dev\/disk[0-9]+$/ {print $1; exit}')
diskutil partitionDisk $D GPT APFS RTEST 100% >/dev/null
V=$(diskutil list $D | awk '/APFS Volume RTEST/ {print $NF; exit}')
[[ -n $V ]] || V=$(diskutil list | awk '/APFS Volume RTEST/ {print $NF; exit}')
diskutil unmount $V >/dev/null 2>&1 || true
diskutil mount -mountOptions nobrowse -mountPoint $W/mnt $V >/dev/null
head -c 20000000 /dev/urandom > $W/mnt/payload; want=$(md5 -q $W/mnt/payload)
diskutil unmount $W/mnt >/dev/null; hdiutil detach $D >/dev/null; D=""

$SSDIMG init $W/repo >/dev/null
$SSDIMG -C $W/repo capture $W/src.img >/dev/null
$SSDIMG -C $W/repo commit -m test >/dev/null
$SSDIMG -C $W/repo bundle $W/bundle --name "Test image" --model TestModel --macos-build 25X1 --segment-size 2M | tail -1
segs=$(ls $W/bundle | grep -c '^stream\.'); (( segs > 1 )) && ok "bundle: $segs stream segments"
$SSDIMG bundle-check $W/bundle | tail -1
$W/ssdinstall list-images $W/bundle | grep -q $'\tname=Test image\tmodel=TestModel\t.*macos_build=25X1' && ok "list-images reads IMAGE"

mkfile -n 400m $W/target.img
$W/ssdinstall install $W/bundle $W/target.img --file --yes --agent $A > $W/install.log
grep -q '^DONE' $W/install.log && grep -q '^INFO verify:' $W/install.log && ok "install into a file + verify ($(grep '^DONE' $W/install.log))"
$W/ssdinstall install $W/bundle $W/target.img --file --yes --agent $A --delta > $W/delta.log
grep -q '^DONE' $W/delta.log && ok "--delta: $(grep '^INFO delta' $W/delta.log)"
D=$(hdiutil attach -imagekey diskimage-class=CRawDiskImage -nomount $W/target.img | awk '$1 ~ /^\/dev\/disk[0-9]+$/ {print $1; exit}')
V=$(diskutil list | awk '/APFS Volume RTEST/ {print $NF}' | tail -1)
diskutil mount readOnly -mountOptions nobrowse -mountPoint $W/mnt $V >/dev/null
[[ $(md5 -q $W/mnt/payload) == $want ]] && ok "the installed volume holds the same file"
diskutil unmount $W/mnt >/dev/null
echo "PASS"
