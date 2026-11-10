#!/bin/sh
# post.sh: the post stage, the set's post patches on the target after the OCLP root patch, as root in the builder VM.
# Each $S/post/NN-NAME/apply.sh runs in order (sh -eu) with SYS (System, read-only: System changes belong in an
# oclp-hook patch), DATA, PRE (Preboot), REC (Recovery) and ESP mounted, P = its own folder (P/inputs holds its lock rows) and
# OCLP = the OCLP source of the build (read-only, on the share).
# Env (from layout.sh uuids.env): SYSU DATU PBU RCU; S = share folder. Writes $S/out-post/post.log; last line "RESULT rc=N".
# umask 022, as Apple's installer and a booted Mac create files: the vmctl agent that starts this script runs with
# umask 077, and every parent directory `install -d`/`mkdir -p` made and every file `cp` wrote inherited it (0700/0600).
# A 0700 /private/var/db/Wallpapers, for example, stops Setup Assistant (uid 248) and the new user from reading the
# wallpaper stills.
umask 022
S=${S:-/private/var/ssdshare}; O=$S/out-post; mkdir -p $O; exec > $O/post.log 2>&1
date; ls $S/post
dev() { diskutil info $1 | awk -F': *' '/Device Identifier/{print $2}'; }
SYSD=$(dev $SYSU); DATD=$(dev $DATU); PBD=$(dev $PBU); RCD=$(dev $RCU)
STORE=$(diskutil info $DATD | awk -F': *' '/APFS Physical Store/{print $2}'); ESPD=${STORE%s*}s1   # layout.sh: s1 = EFI
[ -n "$SYSD" ] && [ -n "$DATD" ] && [ -n "$PBD" ] && [ -n "$RCD" ] && [ -n "$STORE" ] || { echo DEVICE-NOT-FOUND; diskutil list; echo "RESULT rc=4"; exit 4; }
M=/private/var/tmp/post; SYS=$M/sys; DATA=$M/data; PRE=$M/pre; REC=$M/rec; ESP=$M/esp; W=/private/var/tmp/postp
for d in $SYSD $DATD $PBD $RCD $ESPD; do diskutil unmount force $d >/dev/null 2>&1; done
mkdir -p $SYS $DATA $PRE $REC $ESP
mount -o nobrowse,rdonly -t apfs /dev/$SYSD $SYS && mount -o nobrowse -t apfs /dev/$DATD $DATA &&
    mount -o nobrowse -t apfs /dev/$PBD $PRE && mount -o nobrowse -t apfs /dev/$RCD $REC &&
    mount -o nobrowse -t msdos /dev/$ESPD $ESP ||
    { echo MOUNT-FAIL; mount; echo "RESULT rc=4"; exit 4; }
rc=0
for p in $S/post/*/; do
    [ -f $p/apply.sh ] || continue
    n=$(basename $p); t=$(date +%s)
    # off the share and root:wheel: virtiofs files show as uid 99
    rm -rf $W/$n; mkdir -p $W/$n; (cd $p && tar -cf - .) | (cd $W/$n && tar -xf -); chown -R root:wheel $W/$n
    SYS=$SYS DATA=$DATA PRE=$PRE REC=$REC ESP=$ESP P=$W/$n OCLP=$S/oclp sh -eu $W/$n/apply.sh; rc=$?
    echo "STEP $n $(( $(date +%s)-t )) s rc=$rc"; [ $rc = 0 ] || break
done
rm -rf $W
# The VM mounts the target's Data volume the normal way when it starts, before the unmount above, so its Spotlight may
# have started a store there: remove it, as pristine.sh and oclp.sh do (ssdimg's boot check refuses an image with one).
rm -rf $DATA/.Spotlight-V100
for m in $ESP $REC $PRE $DATA $SYS; do umount $m 2>/dev/null || diskutil unmount force $m >/dev/null; done
sync; date; echo "RESULT rc=$rc"
