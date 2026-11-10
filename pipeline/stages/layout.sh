#!/bin/zsh
# layout.sh IMG RUN UNPACK TREE MAP: create the empty target IMG (raw, 80 GB sparse) laid out like a macOS install (GPT +
# EFI + APFS: Data "NAME - Data", Preboot, Recovery, System "NAME" grouped with Data, VM), then fill Preboot/Recovery
# from the build's unpack outputs as the user (stage-pb.py). Writes RUN/uuids.env (NAME GRP SYSU DATU PBU RCU).
set -eu
IMG=${1:?target image}; RUN=${2:?run folder}; UNPACK=${3:?unpack folder}; TREE=${4:?System tree}; MAP=${5:?Preboot map}
NAME=${NAME:-macOS}
mkdir -p $RUN/mnt/pb $RUN/mnt/rc
mkfile -n 80g $IMG
# A blank image attaches as one whole disk; hdiutil's first line is not always it
D=$(hdiutil attach -imagekey diskimage-class=CRawDiskImage -nomount $IMG | awk '$1 ~ /^\/dev\/disk[0-9]+$/ {print $1; exit}')
diskutil partitionDisk $D GPT APFS "$NAME - Data" 100% >/dev/null
C=$(diskutil info ${D}s2 | awk -F": *" "/APFS Container:/{print \$2}")
# partitionDisk mounts the new Data volume: unmount it at once so this Mac's Spotlight/fseventsd leave it alone
diskutil unmount ${C}s1 >/dev/null || true
diskutil apfs changeVolumeRole ${C}s1 D >/dev/null
diskutil apfs addVolume $C APFS Preboot -role B -nomount >/dev/null
diskutil apfs addVolume $C APFS Recovery -role R -nomount >/dev/null
diskutil apfs addVolume $C APFS "$NAME" -role S -groupWith ${C}s1 -nomount >/dev/null
diskutil apfs addVolume $C APFS VM -role V -nomount >/dev/null
uuid() { diskutil info $1 | awk -F': *' '/Volume UUID/{print $2}'; }
vol() { diskutil list $C | awk -v n="$1" '$0 ~ n {print $NF; exit}'; }
DAT=${C}s1; PB=$(vol "APFS Volume Preboot"); RC=$(vol "APFS Volume Recovery")
SYS=$(diskutil list $C | awk -v n="APFS Volume $NAME" '$0 ~ n"[ ]+[0-9]" && !/ - Data/{print $NF; exit}')
GRP=$(uuid $DAT)
print -r -- "NAME='$NAME' GRP=$GRP SYSU=$(uuid $SYS) DATU=$GRP PBU=$(uuid $PB) RCU=$(uuid $RC)" > $RUN/uuids.env
diskutil mount -mountOptions nobrowse -mountPoint $RUN/mnt/pb $PB >/dev/null
diskutil mount -mountOptions nobrowse -mountPoint $RUN/mnt/rc $RC >/dev/null
python3 -I ${0:A:h}/stage-pb.py $MAP $UNPACK $TREE $RUN/mnt/pb $RUN/mnt/rc $GRP > $RUN/stage-pb.tsv 2> $RUN/stage-pb.err
diskutil unmount $RUN/mnt/pb >/dev/null; diskutil unmount $RUN/mnt/rc >/dev/null; hdiutil detach $D >/dev/null
cat $RUN/uuids.env
