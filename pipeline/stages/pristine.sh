#!/bin/sh
# pristine.sh: the pristine stage, the build's pristine System + Data + Preboot on the attached target, as root in the Apple silicon
# builder VM (SIP off, amfi_get_out_of_my_way=1). aa extract with full metadata, hard links, hmeta fix-up, Data
# template, 17 firmlinks (fsctl, entitled fl), updatePreboot, bless --label, bless --create-snapshot.
# Env (from layout.sh uuids.env): NAME GRP SYSU DATU PBU RCU; S = share folder with the pristine stage inputs; OUT = log folder.
# Never runs mdutil -a: it reached the target's volumes and may leave "indexing disabled" on the shipped Data volume.
NAME=${NAME:-macOS}    # boot-picker label = the System volume name
S=${S:-/private/var/ssdshare}; O=${OUT:-$S/out-pristine}; mkdir -p $O; L=$O/pristine.log; exec > $L 2>&1
ts() { date +%s; }; step() { echo "STEP $1 $2 s"; }
[ -n "${SYSU:-}" ] && [ -n "${DATU:-}" ] && [ -n "${PBU:-}" ] && [ -n "${RCU:-}" ] && [ -n "${GRP:-}" ] || { echo "no volume UUIDs (uuids.env)"; echo "RESULT rc=2"; exit 2; }
date; sw_vers; id -un; csrutil status; sysctl -n kern.bootargs
pmset -a sleep 0 displaysleep 0 disksleep 0 2>&1
dev() { diskutil info $1 | awk -F': *' '/Device Identifier/{print $2}'; }
SYS=$(dev $SYSU); DAT=$(dev $DATU); PB=$(dev $PBU); RC=$(dev $RCU); echo "SYS=$SYS DAT=$DAT PB=$PB RC=$RC"
[ -n "$SYS" ] && [ -n "$DAT" ] && [ -n "$PB" ] && [ -n "$RC" ] || { echo DEVICE-NOT-FOUND; diskutil list; exit 1; }
for d in $SYS $DAT $PB $RC; do diskutil unmount force $d >/dev/null 2>&1; done
mkdir -p /Volumes/au-sys /Volumes/au-data /Volumes/au-pb /Volumes/au-rc
mount -o nobrowse -t apfs /dev/$SYS /Volumes/au-sys && mount -o nobrowse -t apfs /dev/$DAT /Volumes/au-data && mount -o nobrowse -t apfs /dev/$PB /Volumes/au-pb && mount -o nobrowse -t apfs /dev/$RC /Volumes/au-rc || { echo MOUNT-FAIL; mount; exit 1; }
R=/Volumes/au-sys; D=/Volumes/au-data; P=/Volumes/au-pb; CSP=$P/$GRP/System/Library/CoreServices
T0=$(ts)
t=$(ts); aa extract -v -i $S/payload.yaa -d $R > $O/aa-extract.out 2> $O/aa-extract.err; echo "aa rc=$?"; step aa-extract $(( $(ts)-t ))
t=$(ts); $S/autool links $R $S/links.txt 2> $O/links.err; echo "links rc=$?"; step links $(( $(ts)-t ))
t=$(ts); aa extract -v -i $S/firmlinks_payload -d $R > /dev/null 2> $O/aa-fl.err; echo "aa firmlinks_payload rc=$?"; step firmlink-dirs $(( $(ts)-t ))
t=$(ts); $S/hmeta apply $R $S/sys.tsv > $O/hmeta-sys.out 2> $O/hmeta-sys.err; echo "hmeta sys rc=$?"; cat $O/hmeta-sys.out; tail -3 $O/hmeta-sys.err; step hmeta-sys $(( $(ts)-t ))
TPL=$R/System/Library/Templates/Data
t=$(ts); ditto $TPL $D; n=0; while IFS="	" read -r p tg; do rm -rf "$D/$p"; mkdir -p "$(dirname "$D/$p")"; ln -s "$tg" "$D/$p" && n=$((n+1)); done < $S/data-symlinks.tsv; echo "symlinks=$n"
$S/hmeta apply $D $S/data.tsv > $O/hmeta-data.out 2> $O/hmeta-data.err; echo "hmeta data rc=$?"; step data $(( $(ts)-t ))
t=$(ts); FLRC=0; while IFS="	" read -r p u g m f tg; do $S/fl set "$R$p" "$tg"; r=$?; [ $r = 0 ] || { echo "SET-FAIL $p rc=$r"; FLRC=$r; }; [ $r -ge 128 ] && break; done < $S/meta17.tsv
echo "FLRC=$FLRC"; $S/fl-noent get $(cut -f1 $S/meta17.tsv | sed "s|^|$R|"); step firmlinks $(( $(ts)-t ))
t=$(ts); $S/autool check $R $S/sys.tsv > $O/check-sys.txt 2> $O/check-sys.sum; cat $O/check-sys.sum; $S/autool check $D $S/data.tsv > $O/check-data.txt 2> $O/check-data.sum; cat $O/check-data.sum; step checks $(( $(ts)-t ))
t=$(ts); $S/hmeta own $P/$GRP 0 0 | tail -1; $S/hmeta own /Volumes/au-rc/$GRP 0 0 | tail -1; chown root:wheel $P /Volumes/au-rc; step pb-rc-owners $(( $(ts)-t ))
umount /Volumes/au-rc
t=$(ts); diskutil apfs updatePreboot $SYS > $O/updatePreboot.out 2>&1; echo "updatePreboot rc=$?"; grep -E "Open Directory|AllUsers|efires|EFILogin" $O/updatePreboot.out | head -5; step updatePreboot $(( $(ts)-t ))
mount | grep -q " $P " || mount -o nobrowse -t apfs /dev/$PB $P
ls $P/$GRP/System/Library/Caches/com.apple.corestorage/EFILoginLocalizations 2>/dev/null | wc -l | sed 's/^/efires: /'
t=$(ts); [ -e $CSP/.root_uuid ] || printf '%s' $GRP > $CSP/.root_uuid; $S/hmeta apply $P/$GRP $S/rootuuid.tsv | tail -1
bless --folder $R/System/Library/CoreServices --bootefi --label "$NAME" > $O/bless-label.out 2>&1; echo "bless label rc=$?"
ls -la $R/System/Library/CoreServices | grep -E "disk_label"; rm -f $R/System/Library/CoreServices/.disk_label $R/System/Library/CoreServices/.disk_label_2x
[ -e $CSP/.disk_label.contentDetails ] || { printf '%s' "$NAME" > $CSP/.disk_label.contentDetails; chown root:wheel $CSP/.disk_label.contentDetails; chmod 644 $CSP/.disk_label.contentDetails; }   # as 01-pristine
ls -la $CSP | grep -E "disk_label|root_uuid"
umount $P; sync
bless --folder $R/System/Library/CoreServices --bootefi --create-snapshot > $O/bless.out 2>&1; echo "bless snapshot rc=$?"; cat $O/bless.out; step bless $(( $(ts)-t ))
diskutil apfs listSnapshots $SYS | grep -E "Name|XID|root"; diskutil info $SYS | grep -E "Sealed|Volume Group"
ls -la $R/System/Library/CoreServices | grep -E "disk_label|x86legacy"
echo "TOTAL $(( $(ts)-T0 )) s"
rm -rf $D/.Spotlight-V100 $R/.Spotlight-V100   # no Spotlight store from this VM on the shipped volumes
for m in $D $R; do umount $m 2>/dev/null || diskutil unmount force $m >/dev/null; done; sync; date; echo "RESULT rc=0"
