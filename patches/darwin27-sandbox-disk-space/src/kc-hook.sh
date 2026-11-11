#!/bin/sh
# darwin27-sandbox-disk-space (kc-hook, 27.0 26A428 sets only): the Darwin 27 kernel asks the Sandbox
# system-info "vfs.disk-space" on every statfs, fstatfs, getattrlist volume-space and quota call; 26A428's
# system.sb (and Tahoe's) has no rule for it, so every (deny default) profile that imports system.sb denies and
# reports each call. Adds macOS 27.2's rule (26B123's system.sb is 26A428's plus this block) with
# disk_space_rule.py. 27.2 and later already have the rule: the file is then left untouched.
set -eu
S=${1:?System mount}; H=$(cd "$(dirname "$0")" && pwd)
F=$S/System/Library/Sandbox/Profiles/system.sb
W=$(mktemp -d /private/var/tmp/darwin27-sandbox-disk-space.XXXXXX)
"${OCLP_PYTHON:?}" -I "$H/disk_space_rule.py" "$F" "$W/system.sb"
if cmp -s "$F" "$W/system.sb"; then
    rm -rf "$W"
    echo "darwin27-sandbox-disk-space: this build's system.sb already has the rule; left unchanged (drop this entry from the set)"
    exit 0
fi
rm -f "$F"
install -o root -g wheel -m 644 "$W/system.sb" "$F"
chflags restricted "$F"
rm -rf "$W"
shasum -a 256 "$F"
