#!/bin/sh
# golden-gate-kernelmanager-helper-warm (kc-hook): Apple's LaunchDaemon com.apple.kernelmanager_helper gets
# RunAtLoad=true and POSIXSpawnType Interactive, in its plist and in its entry of launchd's job cache, so the helper
# is up when kernelmanagerd asks it for the load reports of the kexts OpenCore injects. warm.py writes both; each is
# put back root:wheel 0644 restricted, like Apple's. After golden-gate-userspace, which also edits the job cache.
set -eu
S=${1:?System mount}; H=$(cd "$(dirname "$0")" && pwd)
J=$S/System/Library/LaunchDaemons/com.apple.kernelmanager_helper.plist
C=$S/System/Library/xpc/launchd.plist
W=$(mktemp -d /private/var/tmp/kernelmanager-helper-warm.XXXXXX)
"${OCLP_PYTHON:?}" -I "$H/warm.py" "$J" "$C" "$W"
for pair in "job.plist:$J" "cache.plist:$C"; do
    f=${pair%%:*}; F=${pair#*:}
    chflags noschg,nouchg,norestricted "$F"
    rm -f "$F"
    install -o root -g wheel -m 644 "$W/$f" "$F"
    chflags restricted "$F"
    echo "golden-gate-kernelmanager-helper-warm: $(shasum -a 256 "$F" | cut -c1-16) $F"
done
rm -rf "$W"
