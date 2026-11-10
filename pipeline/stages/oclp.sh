#!/bin/sh
# oclp.sh SYSTEM_UUID: OCLP root patch (the oclp stage) of the attached target, as root in the Apple silicon builder VM
# (macOS 26.x, SIP off, amfi_get_out_of_my_way=1). Inputs from inputs.sh in the share ($S).
# The target System volume is mounted at oclp-target/disk3s4 (--target_mount_name): kmutil records that path in the
# kernel collections, so a fixed name keeps the KCs identical from build to build.
# An empty hooks folder = the stock OCLP root patch.
# One builder session per segment (pipeline/run restarts the VM in between and keeps the target as a cache point):
#   KC_HOOK empty: copy the inputs, run OCLP (and so every hook.sh); log $S/out-oclp/oclp.log.
#   KC_HOOK=NAME:  run the one kc-hook NAME on the live System; log $S/out-oclp/oclp-NAME.log.
# A segment that leaves a kc-hook to run prints "NEXT kc-hook NAME" (no snapshot yet); the last one makes the snapshot.
# Last line "RESULT rc=N".
# umask 022, as on a Mac where OCLP runs: the vmctl agent that starts this script has umask 077, so whatever OCLP and
# the hooks wrote without an explicit mode would be owner-only (OCLP's root-patch record
# System/Library/CoreServices/OpenCore-Legacy-Patcher.plist would be 0600 instead of 0644).
umask 022
S=${S:-/private/var/ssdshare}; SYSU=${1:?target System volume UUID}
export GRP PBU   # hooks find the target's volume group and Preboot from these (uuids.env), not from diskutil labels
KC_HOOK=${KC_HOOK:-}; O=$S/out-oclp; mkdir -p $O; L=$O/oclp${KC_HOOK:+-$KC_HOOK}.log; exec > $L 2>&1
W=/private/var/tmp/oclp; ts() { date +%s; }
date; sw_vers | tr '\n' ' '; echo; uname -m; csrutil status; sysctl -n kern.bootargs; cat $S/MANIFEST.txt
SYS=$(diskutil info $SYSU | awk -F': *' '/Device Identifier/{print $2}')
[ -n "$SYS" ] || { echo "target $SYSU not found"; diskutil list; echo "RESULT rc=4"; exit 4; }
diskutil info $SYS | grep -E "Volume Name|Device Identifier|Sealed|Volume Group"
pmset -a sleep 0 displaysleep 0 disksleep 0 >/dev/null 2>&1
t=$(ts); rm -rf $W; mkdir -p $W
# Everything is copied to a local disk and made root:wheel: files on the virtiofs share show as uid 99, and OCLP merges
# the KDK into the target with rsync -a, which keeps the owner; kmutil then skips every uid-99 kext (BootKC without the
# kernel's KPIs, "names a dependency on 'com.apple.kpi.*', which was not found"). tar, not ditto: ditto follows the
# Python runtime's symlinks over virtiofs (ELOOP).
# A kc-hook segment needs only the Python and the hooks; OCLP and the KDK only OCLP's own run.
D="oclp py site kdk"; [ -n "$KC_HOOK" ] && D="py site"
for d in $D; do mkdir -p $W/$d && (cd $S/$d && tar -cf - .) | (cd $W/$d && tar -xf -) || { echo "copy $d failed"; echo "RESULT rc=3"; exit 3; }; done
chown -R root:wheel $W
# Large immutable images need no guest-side ownership rewrite. Keep declared inputs
# on virtiofs; their mounted filesystem metadata is independent of the backing file's uid.
$W/py/bin/python3.12 -I "$S/copy-hooks.py" "$S/hooks" "$W/hooks" --owner root:wheel || { echo "copy hooks failed"; echo "RESULT rc=3"; exit 3; }
echo "STEP copy $(( $(ts)-t )) s"
export PYTHONPATH=$W/site PYTHONDONTWRITEBYTECODE=1
rc=0
if [ -z "$KC_HOOK" ]; then   # OCLP's run: the first segment only
    # Wall time per oclp hook: OCLP runs them (run_hooks) and logs no times, so each hook.sh is wrapped. The hook itself
    # moves to .hook.sh in its own folder, so its "$(dirname "$0")" is unchanged; the wrapper passes arguments,
    # environment, output and exit status through and appends "STEP hook NAME N s" to a file printed after OCLP.
    for h in $W/hooks/*/hook.sh; do
        [ -f "$h" ] || continue
        d=$(dirname $h); n=$(basename $d); mv $h $d/.hook.sh
        printf '%s\n' 't=$(date +%s); /bin/sh "$(dirname "$0")/.hook.sh" "$@"; rc=$?' \
            "echo \"STEP hook ${n#??-} \$(( \$(date +%s)-t )) s\" >> $W/hook-times; exit \$rc" > $h
    done
    t=$(ts)
    HOOKS=; [ -n "$(ls $W/hooks)" ] && HOOKS="--hooks $W/hooks"
    $W/py/bin/python3.12 $W/oclp/ssd-target/run.py --patch_sys_vol --target $SYS --allow_build_mismatch \
        --kdk_dir $W/kdk $HOOKS --hardware_profile $S/profile.json --target_mount_name disk3s4
    rc=$?; [ -f $W/hook-times ] && cat $W/hook-times; echo "STEP oclp $(( $(ts)-t )) s"; echo "OCLP exit $rc"
    cp /Users/Shared/OpenCore-Patcher_*.log $O/ 2>/dev/null
fi
# kc-hook.sh (a hook folder's, instead of or next to hook.sh) replaces what OCLP's kmutil wrote, so it runs after OCLP,
# on the live System with arg SYSTEM_MOUNT, each in its own segment (set order); after the last one a new snapshot
# (made as OCLP makes it) replaces OCLP's. NEXT = the kc-hook after this segment's (after OCLP: the first one).
NEXT=; prev=
for h in $W/hooks/*/kc-hook.sh; do
    [ -f "$h" ] || continue
    n=$(basename $(dirname $h)); n=${n#??-}
    [ -z "$NEXT" ] && [ "$prev" = "$KC_HOOK" ] && NEXT=$n
    prev=$n
done
if [ $rc = 0 ] && [ -n "$KC_HOOK" ]; then
    t=$(ts); M=/private/var/tmp/oclp-sys; mkdir -p $M
    # a new session: DiskArbitration has mounted the live System read-only, and APFS refuses a second mount (OCLP's
    # target.py unmounts it the same way)
    diskutil unmount $SYS >/dev/null 2>&1
    mount -o nobrowse -t apfs /dev/$SYS $M || rc=5
    OLD=$(diskutil apfs listSnapshots $M | awk -F': *' '/Name:/{print $2}')
    if [ $rc = 0 ]; then
        echo "kc-hook $KC_HOOK"; OCLP_PYTHON=$W/py/bin/python3.12 sh $W/hooks/??-$KC_HOOK/kc-hook.sh $M || rc=5
        echo "STEP kc-hook $KC_HOOK $(( $(ts)-t )) s"
    fi
    if [ $rc = 0 ] && [ -z "$NEXT" ]; then
        bless --mount $M --create-snapshot || rc=5
        [ $rc = 0 ] && for s in $OLD; do diskutil apfs deleteSnapshot $SYS -name $s || rc=5; done
    fi
    diskutil apfs listSnapshots $M | grep -E "Name|XID"; umount $M
fi
[ $rc = 0 ] && [ -n "$NEXT" ] && echo "NEXT kc-hook $NEXT"
diskutil apfs listSnapshots $SYS | grep -E "Name|XID"
# OCLP mounts the target Data volume the normal way, so this VM's Spotlight may have started a store on it: remove it
if [ -n "${DATU:-}" ]; then
    DAT=$(diskutil info $DATU | awk -F': *' '/Device Identifier/{print $2}')
    diskutil unmount $DAT >/dev/null 2>&1; mkdir -p /private/var/tmp/oclp-data
    mount -o nobrowse -t apfs /dev/$DAT /private/var/tmp/oclp-data && {
        rm -rf /private/var/tmp/oclp-data/.Spotlight-V100; ls -a /private/var/tmp/oclp-data | tr '\n' ' '; echo
        umount /private/var/tmp/oclp-data; }
fi
sync; date; echo "RESULT rc=$rc"
