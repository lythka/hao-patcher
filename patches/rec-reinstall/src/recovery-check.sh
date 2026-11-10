#!/bin/sh
# recovery-check.sh BUNDLE [BIN]: non-destructive checks of ssdinstall in a Recovery that carries it (Utilities >
# Terminal; BIN defaults to /usr/local/ssdtools). Writes nothing to the internal disk or NVRAM: the bundle
# is installed into a file in RAM. BUNDLE = a small test bundle on a USB disk (rec-reinstall README, "Verify":
# test.sh -k keeps one); a full image does not fit in RAM.
B=${2:-/usr/local/ssdtools}; IMG=${1:?bundle}
I=$B/ssdinstall; A=$B/bin/ssdpush-agent; W=/private/var/tmp/ssdinstall-test
set -u
echo "== 1. list-disks (expect the internal disk only; never the USB disk or the RAM disk)"
$I list-disks; echo "rc=$?"
echo "== 2. list-images (expect the bundle on the USB disk)"
$I list-images; $I list-images "$IMG"
size=$(awk -F= '$1 == "size" {print $2}' "$IMG/IMAGE")
echo "== 3. install the bundle into a RAM file of the image's size ($size bytes) and verify, then again with --delta"
mkdir -p $W && mkfile -n $size $W/t.img
$I install "$IMG" $W/t.img --file --yes --agent $A | grep -v '^PROGRESS'; echo "rc=$?"
$I install "$IMG" $W/t.img --file --yes --agent $A --delta | grep -E '^(INFO delta|INFO verify|DONE|ERROR)'; echo "rc=$?"
echo "== 4. the result as a disk"
d=$(hdiutil attach -nomount -imagekey diskimage-class=CRawDiskImage $W/t.img | awk 'NR==1 {print $1}')
diskutil list $d; hdiutil detach $d -force >/dev/null; rm -rf $W
echo "== 5. refusals: the USB disk holding the bundle (not internal)"
s=$(diskutil info -plist "$IMG" | plutil -extract ParentWholeDisk raw -o - -)
$I install "$IMG" /dev/$s --yes 2>&1 | grep ERROR
echo "== 6. bootguard state and the firmware default (startup-internal keeps a NetBoot default)"
launchctl print system/local.bootguard 2>/dev/null | grep -E 'state|pid' | head -3
nvram efi-boot-device 2>/dev/null | head -c 300; echo
