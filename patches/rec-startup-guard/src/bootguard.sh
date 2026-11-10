#!/bin/sh
# bootguard.sh (local.bootguard, at boot): find the internal ESP that holds \EFI\OC\OpenCore.efi and run bootguard on
# it (see bootguard.c: Startup Disk must not leave the firmware pointing at Tahoe's boot.efi). No internal OpenCore:
# nothing to protect, exits. Waits up to 2 minutes for the internal disk.
# Every internal, non-removable disk is tried in turn (internal-esp with IESP_DISKS=<one disk>), not only the first EFI partition found: a second internal drive (optical-bay caddy) with
# its own ESP must not hide the boot drive's OpenCore. Nothing is machine-specific: the ESP's partition UUID is read
# at run time, so one end-user image fits every laptop.
D=$(dirname "$0"); IESP=$D/internal-esp
internal_disks() {
    l=$(diskutil list -plist internal physical)
    n=$(echo "$l" | plutil -extract WholeDisks raw -o - - 2>/dev/null) || n=0
    j=0
    while [ "$j" -lt "$n" ]; do
        d=$(echo "$l" | plutil -extract "WholeDisks.$j" raw -o - -)
        [ "$(diskutil info -plist "$d" | plutil -extract Removable raw -o - - 2>/dev/null)" = true ] || echo "$d"
        j=$((j + 1))
    done
}
i=0; seen=0
while [ $i -lt 60 ]; do
    for d in $(internal_disks); do
        r=$(IESP_DISKS=$d sh "$IESP" --mount 2>/dev/null) || continue
        seen=1
        set -- $r; esp=$1; mp=$2; mounted=$3
        oc=no; [ -f "$mp/EFI/OC/OpenCore.efi" ] && oc=yes
        [ "$mounted" = 1 ] && diskutil unmount "$mp" >/dev/null 2>&1
        [ $oc = yes ] || { echo "bootguard: $esp ($d) has no \\EFI\\OC\\OpenCore.efi"; continue; }
        uuid=$(diskutil info -plist "$esp" | plutil -extract DiskUUID raw -o - - 2>/dev/null)
        [ -n "$uuid" ] || { echo "bootguard: no partition UUID for $esp"; exit 1; }
        exec "$D/bootguard" "$uuid" "$esp"
    done
    [ $seen = 1 ] && { echo "bootguard: no internal ESP with OpenCore: nothing to guard"; exit 0; }
    sleep 2; i=$((i + 1))
done
echo "bootguard: no internal ESP"; exit 0
