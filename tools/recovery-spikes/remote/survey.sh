#!/bin/sh
# Runs ON THE LAPTOP in the NetBoot Recovery, as root. Read-only: prints facts, mounts and writes nothing.
#   sh survey.sh
# One "SURVEY key value" line per fact; run.sh's verdict turns them into survey.json and a RAM budget per spike.
set -u
s() { printf 'SURVEY %s %s\n' "$1" "$(printf '%s' "$2" | tr '\n' ' ' | sed 's/ *$//')"; }
sha() { [ -f "$1" ] || { echo absent; return; }; command -v shasum >/dev/null 2>&1 && shasum -a 256 "$1" | cut -d' ' -f1 || echo "md5:$(md5 -q "$1")"; }
APFS=/System/Library/Filesystems/apfs.fs/Contents/Resources
s build "$(sw_vers -buildVersion)"
s product "$(sw_vers -productVersion)"
s arch "$(uname -m)"
s kernel "$(uname -v)"
s model "$(sysctl -n hw.model)"
s memsize "$(sysctl -n hw.memsize)"
s ncpu "$(sysctl -n hw.ncpu)"
s mem_available_mb "$(sh "$(dirname "$0")/common.sh" mem)"
s boot_args "$(nvram boot-args 2>/dev/null | cut -f2-)"
s csr "$(csrutil status 2>&1 | head -1)"
s csr_active_config "$(nvram csr-active-config 2>/dev/null | cut -f2-)"
for t in perl codesign ditto hdiutil diskutil bless kmutil tar shasum install rsync plutil chflags mkfile; do
    s "tool_$t" "$(command -v $t || echo absent)"
done
s newfs_apfs "$(sha $APFS/newfs_apfs)"
s apfs_prepare_cryptex "$(sha $APFS/apfs_prepare_cryptex)"
s apfs_systemsnapshot "$(sha $APFS/apfs_systemsnapshot)"
s kmutil_sha256 "$(sha /usr/bin/kmutil)"
s apfs_framework "$([ -e /System/Library/PrivateFrameworks/APFS.framework ] && echo present || echo absent)"
s root_device "$(diskutil info / | awk -F': *' '/Device Identifier/ {print $2}')"
s root_writable "$(diskutil info / | awk -F': *' '/Volume Read-Only/ {print $2}')"
df -k | awk 'NR > 1 { printf "SURVEY df %s %s KB free of %s KB at %s\n", $1, $4, $2, $NF }'
diskutil list internal physical 2>/dev/null | awk '/^\/dev\// { print "SURVEY internal_disk " $1 }'
diskutil apfs list 2>/dev/null | awk '
    /APFS Physical Store Disk:/ { store = $NF }
    /\+-> Volume / { vol = $3; role = "" }
    /APFS Volume Disk \(Role\):/ { role = $NF; gsub(/[()]/, "", role) }
    /Name:/ && vol { sub(/^.*Name: */, ""); print "SURVEY apfs_volume " vol " role " role " store " store " name " $0; vol = "" }'
mount | awk '{ print "SURVEY mount " $0 }'
echo "END survey rc=0"
