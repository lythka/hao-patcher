#!/bin/zsh
# esp.sh IMG RUN TAR: fill the target's (empty) EFI system partition with TAR, the target's ESP as OCLP installs it
# (EFI/OC + System/Library/CoreServices/boot.efi bootstrap; targets/<model>.json "esp").
set -eu
IMG=${1:?target image}; RUN=${2:?run folder}; TAR=${3:?ESP tar}
# The GPT whole disk, not hdiutil's first line: that can be the APFS container, whose s1 is Data
D=$(hdiutil attach -imagekey diskimage-class=CRawDiskImage -nomount $IMG | awk '/GUID_partition_scheme/ {print $1; exit}')
M=$RUN/mnt/esp; mkdir -p $M
diskutil mount -mountOptions nobrowse -mountPoint $M ${D}s1 >/dev/null
tar -xf $TAR -C $M
find $M -name '._*' -delete
diskutil unmount $M >/dev/null; hdiutil detach $D >/dev/null
echo "esp.sh: ESP = $(basename $TAR)"
