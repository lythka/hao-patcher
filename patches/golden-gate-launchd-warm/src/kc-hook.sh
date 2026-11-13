#!/bin/sh
# golden-gate-launchd-warm (kc-hook): Apple LaunchDaemons that boot waits on start with launchd, at Interactive
# priority: com.apple.kernelmanager_helper (RunAtLoad), for the load reports of the kexts OpenCore injects, and
# com.apple.mbsystemadministration (KeepAlive PathState: while /private/var/db/.AppleSetupDone is missing), which
# loginwindow waits for before Setup Assistant. warm.py writes each job plist and launchd's job cache; each is put
# back root:wheel 0644 restricted, like Apple's. After golden-gate-userspace, which also edits the job cache.
set -eu
S=${1:?System mount}; H=$(cd "$(dirname "$0")" && pwd)
W=$(mktemp -d /private/var/tmp/launchd-warm.XXXXXX)
"${OCLP_PYTHON:?}" -I "$H/warm.py" "$S" "$W"
for f in System/Library/LaunchDaemons/com.apple.kernelmanager_helper.plist \
         System/Library/LaunchDaemons/com.apple.mbsystemadministration.plist \
         System/Library/xpc/launchd.plist; do
    F=$S/$f
    chflags noschg,nouchg,norestricted "$F"
    rm -f "$F"
    install -o root -g wheel -m 644 "$W/$f" "$F"
    chflags restricted "$F"
    echo "golden-gate-launchd-warm: $(shasum -a 256 "$F" | cut -c1-16) $F"
done
rm -rf "$W"
