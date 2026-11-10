#!/bin/sh
# recovery.sh: the Recovery (BaseSystem) with the set's recovery patches, as root in the builder VM (SIP off). Its text
# runs through vmctl exec (pipeline/recovery's guest()), never from the share.
#   1. $S/in/BaseSystem.dmg -> UDRW copy on the VM's disk, grown to GROW, its "macOS Base System" volume mounted at BS
#   2. the System volume SYSU of the target image (attached to the VM as a USB disk) mounted read-only at SYS
#   3. each $S/rec/NN-NAME/apply.sh in set order (sh -eu) with BS, SYS and P = its own folder (src/, build/, inputs/)
#   4. when a patch has kexts.txt: BaseSystemKernelExtensions.kc rebuilt by SYS's kmutil and kernel from the
#      BaseSystem's own list (.bundles, .elides) + those kexts. The patched /S/L/E is mounted over this VM's
#      /System/Library/Extensions for the create: kmutil records each kext's path as it read it, and kexts load their
#      resources from that path at boot (with -R <scratch volume> it would store /private/var/tmp/... paths and the KC
#      would be rejected). KC and .bundles are written with cat (stock owner and mode kept).
#   5. UDRO -> $S/out/BaseSystem.dmg
# Env: S (share), SYSU (System volume UUID), GROW (default 3200m). Log $S/out/recovery.log, last line "RESULT rc=N".
# umask 022 as in post.sh and oclp.sh: vmctl's agent runs with 077, so folders the rec-* patches create implicitly
# (install -d, mkdir -p, cp) would be 0700/0600 in the BaseSystem.
umask 022
S=${S:-/private/var/ssdshare}; GROW=${GROW:-3200m}; O=$S/out; mkdir -p $O; exec > $O/recovery.log 2>&1
W=/private/var/tmp/recovery; BS=$W/bs; SYS=$W/sys; KCR=$W/sle; E=/System/Library/Extensions
KCD=System/Library/KernelCollections
fail() { echo "FAIL: $*"; echo "RESULT rc=1"; exit 1; }
DEV=""; KDEV=""; OVER=0
cleanup() { { [ $OVER = 1 ] && umount $E; umount $KCR; umount $BS; umount $SYS
              [ -n "$KDEV" ] && hdiutil detach -force $KDEV; [ -n "$DEV" ] && hdiutil detach -force $DEV; } >/dev/null 2>&1; }
trap cleanup EXIT
attach() {   # IMAGE VOLNAME: attach unmounted; ADEV = whole disk, AVOL = its APFS volume VOLNAME
    out=$(hdiutil attach -nomount -noverify -noautofsck "$1") || return 1
    ADEV=$(echo "$out" | awk '$2 == "GUID_partition_scheme" {print $1; exit}'); AVOL=""
    for d in $(echo "$out" | awk '$1 ~ /s[0-9]+$/ {print $1}'); do
        [ "$(diskutil info -plist $d | plutil -extract VolumeName raw -o - - 2>/dev/null)" = "$2" ] && AVOL=$d
    done
    [ -n "$AVOL" ]
}
ver() { plutil -extract ProductBuildVersion raw -o - $1/System/Library/CoreServices/SystemVersion.plist; }
T0=$(date +%s); date; echo "builder $(sw_vers -buildVersion) $(uname -m), $(id -un)"
rm -rf $W; mkdir -p $W $BS $SYS $KCR

# 1. writable, grown BaseSystem
cp $S/in/BaseSystem.dmg $W/ || fail "copy BaseSystem.dmg"
hdiutil convert $W/BaseSystem.dmg -format UDRW -o $W/rw.dmg >/dev/null || fail "convert to UDRW"
hdiutil resize -size $GROW -imageonly $W/rw.dmg || fail "resize to $GROW"
attach $W/rw.dmg "macOS Base System" || fail "no macOS Base System volume in BaseSystem.dmg"; DEV=$ADEV
diskutil apfs resizeContainer ${DEV}s1 0 >/dev/null || fail "APFS container resize"
diskutil unmount $AVOL >/dev/null 2>&1
mount -t apfs -o nobrowse $AVOL $BS || fail "mount $AVOL"
echo "BaseSystem $(ver $BS), $(df -h $BS | awk 'NR == 2 {print $4}') free"

# 2. the target's patched System, read-only
SYSD=$(diskutil info $SYSU | awk -F': *' '/Device Identifier/{print $2}')
diskutil unmount $SYSD >/dev/null 2>&1
mount -t apfs -o nobrowse,rdonly /dev/$SYSD $SYS || fail "mount System $SYSU ($SYSD)"
echo "System $(ver $SYS) ($SYSD)"
echo "STEP setup $(( $(date +%s) - T0 )) s"

# 3. patches
: > $W/kexts
for p in $S/rec/*/; do
    [ -d $p ] || continue
    n=$(basename $p); t=$(date +%s)
    # off the share and root:wheel: virtiofs files show as uid 99
    mkdir -p $W/p/$n; (cd $p && tar -cf - .) | (cd $W/p/$n && tar -xf -); chown -R root:wheel $W/p/$n
    BS=$BS SYS=$SYS P=$W/p/$n sh -eu $W/p/$n/apply.sh || fail "$n"
    [ -f $W/p/$n/kexts.txt ] && cat $W/p/$n/kexts.txt >> $W/kexts
    echo "STEP $n $(( $(date +%s) - t )) s"
done

# 4. kernel collection
if [ -s $W/kexts ]; then
    t=$(date +%s); K=$W/kc; mkdir -p $K
    hdiutil create -size 4g -fs APFS -volname recsle -type SPARSE $W/sle >/dev/null || fail "create the /S/L/E volume"
    attach $W/sle.sparseimage recsle || fail "attach the /S/L/E volume"; KDEV=$ADEV; KV=$AVOL
    diskutil unmount $KV >/dev/null 2>&1
    mount -t apfs -o nobrowse $KV $KCR && ditto $SYS$E $KCR && rm -rf $KCR/.fseventsd && umount $KCR ||
        fail "copy the patched /S/L/E"
    cp $SYS/usr/bin/kmutil $SYS/System/Library/Kernels/kernel $K/ && codesign -s - -f $K/kmutil >/dev/null 2>&1 ||
        fail "kmutil and kernel from the System"
    cp $BS/$KCD/BootKernelExtensions.kc $K/boot.kc
    set --
    for id in $(cat $BS/$KCD/BaseSystemKernelExtensions.kc.bundles $W/kexts); do set -- "$@" -b $id; done
    for id in $(cat $BS/$KCD/BaseSystemKernelExtensions.kc.elides); do set -- "$@" --elide-identifier $id; done
    mount -t apfs -o nobrowse,rdonly $KV $E || fail "mount the patched /S/L/E over $E"; OVER=1
    $K/kmutil create -a x86_64 -V release --no-variant-extension --allow-missing-kdk -n sys -k $K/kernel \
        -B $K/boot.kc -S $K/sys.kc -r $E -x "$@" > $O/kmutil.log 2>&1
    rc=$?
    umount $E || umount -f $E || fail "unmount the patched /S/L/E from $E"; OVER=0
    tail -20 $O/kmutil.log
    [ $rc = 0 ] && [ -s $K/sys.kc ] || fail "kmutil rc=$rc"
    cat $K/sys.kc > $BS/$KCD/BaseSystemKernelExtensions.kc
    sort -u $BS/$KCD/BaseSystemKernelExtensions.kc.bundles $W/kexts > $K/bundles
    cat $K/bundles > $BS/$KCD/BaseSystemKernelExtensions.kc.bundles
    ls -ln $BS/$KCD
    hdiutil detach $KDEV >/dev/null 2>&1; KDEV=""
    echo "STEP kc $(sort -u $W/kexts | wc -l | tr -d ' ') kexts added $(( $(date +%s) - t )) s"
fi

# 5. out
t=$(date +%s); df -h $BS | tail -1
sync; umount $BS || fail "unmount the BaseSystem"
hdiutil detach $DEV >/dev/null || hdiutil detach -force $DEV >/dev/null; DEV=""
umount $SYS
hdiutil convert $W/rw.dmg -format UDRO -o $W/out.dmg >/dev/null || fail "convert to UDRO"
rm -f $O/BaseSystem.dmg; cp $W/out.dmg $O/BaseSystem.dmg || fail "copy out"
echo "STEP out $(( $(date +%s) - t )) s"
rm -rf $W; echo "total $(( $(date +%s) - T0 )) s"; date; echo "RESULT rc=0"
