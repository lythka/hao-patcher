#!/bin/bash
# Workflow plan step 9a laptop run: the live backend of `hao patch` on the MacBookPro9,2 test laptop, by the holder
# of the laptop lock, one step at a time (two reboots in between are the lock holder's own steps).
#
#   tools/hao/laptop/run-9a.sh prepare        copy hao + an x86_64 Python to the laptop, make the test overlay there
#   tools/hao/laptop/run-9a.sh probe          hao patch --probe (read-only)
#   tools/hao/laptop/run-9a.sh dry-run        what the patch would write (read-only)
#   tools/hao/laptop/run-9a.sh apply          write it, take the snapshots
#   (reboot 1: tools/hao/laptop/run-9a.sh reboot-note)
#   tools/hao/laptop/run-9a.sh verify-applied running the patch? did the old snapshots survive the boot?
#   tools/hao/laptop/run-9a.sh revert         restore the files, tag the snapshot booted before
#   (reboot 2)
#   tools/hao/laptop/run-9a.sh verify-reverted
#   tools/hao/laptop/run-9a.sh collect        records, logs and snapshot lists into EVIDENCE
#
# The test overlay (made on the laptop from its own files, tools/hao/laptop/hao9a.py):
#   usr/share/hao/9a-probe.txt                           new, System volume (seen only after the reboot)
#   usr/share/misc/ascii                                 replaced (the ascii(7) table plus one comment line), System
#   Library/Application Support/hao-9a/data-probe.txt    new, Data volume (seen at once)
#
# Environment: LAPTOP_SSH (default $HOME/hao-tools/tools/laptop-ssh, with its LAPTOP/LAPTOP_KEY), PY_TGZ (the
# x86_64 python-build-standalone archive, sha256 below), EVIDENCE (default
# $HOME/hao-research/store/evidence/hao-9a-YYYYMMDD), LOCK (default $HOME/mac-image/store/tmp/laptop.lock).
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
repo=$(cd "$here/../../.." && pwd)
ssh_tool=${LAPTOP_SSH:-$HOME/hao-tools/tools/laptop-ssh}
py_tgz=${PY_TGZ:-$repo/store/artifacts/python-x86_64-3.12.14-20261109-0d6a4a299908/cpython-3.12.14+20260924-x86_64-apple-darwin-install_only.tar.gz}
py_sha=0d6a4a299908123f00bc844df737603f047ff9eba14fda6cad83f3cf3cb3a2af
evidence=${EVIDENCE:-$HOME/hao-research/store/evidence/hao-9a-$(date +%Y%m%d)}
lock=${LOCK:-$HOME/mac-image/store/tmp/laptop.lock}
W=/private/var/tmp/hao-9a                 # on the laptop
HAO="$W/python/bin/python3 -I $W/hao/hao patch"
OV=$W/overlay
die() { echo "run-9a: $*" >&2; exit 1; }
on() { "$ssh_tool" -t 900 -- "$@"; }
expect() {   # expect FILE PATTERN...: every pattern (an extended regex) must match a line of FILE
    f=$1; shift
    for p in "$@"; do grep -Eq -- "$p" "$f" || { echo "MISSING: $p" >&2; return 1; }; done
    echo "expected output present ($# lines checked)"
}
step=${1:-}
[ -n "$step" ] || { sed -n '2,/^set -euo/p' "$0" | sed '$d; s/^# \{0,1\}//'; exit 2; }
[ -e "$lock" ] || echo "run-9a: note: $lock does not exist; rebooting and patching the laptop belong to its holder" >&2
mkdir -p "$evidence"
log=$evidence/$step.txt
case $step in
prepare)
    [ -f "$py_tgz" ] || die "no $py_tgz (PY_TGZ)"
    [ "$(shasum -a 256 "$py_tgz" | cut -d' ' -f1)" = "$py_sha" ] || die "$py_tgz: sha256 is not $py_sha"
    t=$(mktemp -d); trap 'rm -rf "$t"' EXIT
    tar -C "$repo/tools" -cf "$t/hao.tar" --exclude __pycache__ --exclude tests hao
    on "rm -rf $W && mkdir -p $W && chmod 700 $W"
    "$ssh_tool" --put "$t/hao.tar" "$W/hao.tar"
    "$ssh_tool" --put "$py_tgz" "$W/python.tar.gz"
    on "cd $W && tar -xf hao.tar && tar -xzf python.tar.gz && [ \"\$(shasum -a 256 python.tar.gz | cut -d' ' -f1)\" = $py_sha ] \
        && $W/python/bin/python3 -I -c 'import ctypes, plistlib, sys; print(sys.version.split()[0], sys.platform)' \
        && $W/python/bin/python3 -I $W/hao/laptop/hao9a.py overlay $OV && $W/python/bin/python3 -I $W/hao/laptop/hao9a.py verify before $OV" | tee "$log"
    # 3.12.14 darwin; three overlay lines (ascii: before X after Y; the new files: before none);
    # PASS usr/share/hao/9a-probe.txt is absent, PASS usr/share/misc/ascii is X, PASS ... data-probe.txt is absent
    expect "$log" '^3\.12\.14 darwin$' '^PASS usr/share/hao/9a-probe.txt is absent' '^before: 0 failed$' ;;
probe)
    on "$HAO --probe" | tee "$log" || true
    on "$HAO --probe --log json" > "$evidence/probe.json" || true
    # every step "ok" except "revert: bless --last-sealed-snapshot" (an image hao built has no sealed snapshot)
    expect "$log" '^machine x86_64, euid 0, csr 0x[0-9a-f]+, authenticated root (disabled|unknown)' \
        '^booted: [^ ]+ \(' '^ok   find the System and Data volumes' \
        '^ok   SIP allows' '^ok   mount the System volume read-write' '^ok   name a snapshot first' \
        '^ok   revert: root-to the booted snapshot' '^NO   revert: bless --last-sealed-snapshot: no sealed snapshot' \
        '^live patch would run$' ;;
dry-run)
    on "$HAO $OV --live --dry-run" | tee "$log"
    expect "$log" '^3 to write, 0 to set attributes on, 0 already current$' \
        '^  new    hao-9a +Library/Application Support/hao-9a/data-probe.txt$' \
        '^  new    hao-9a +usr/share/hao/9a-probe.txt$' '^  write  hao-9a +usr/share/misc/' ;;
apply)
    on "$HAO $OV --live --i-have-a-backup --show" | tee "$log"
    on "$HAO --list --live; diskutil apfs listSnapshots /; ls -lO /usr/share/hao '/Library/Application Support/hao-9a' 2>&1" | tee -a "$log"
    # live: System diskNsM, Data ..., booted com.apple.bless.<A> (sealed: No)
    # 3 to write ...; mounted /dev/diskNsM read-write at /private/var/run/hao-live-system
    # named snapshot hao-pre-hao-<ID> taken before writing; new/new/write lines; next boot: new snapshot <B>
    # applied hao-<ID>: 3 written, 0 attributes set, 0 unchanged, snapshot com.apple.bless.<B>
    # The running system does not have usr/share/hao yet (System files arrive with the reboot); the Data file is there.
    expect "$log" '^live: System disk[0-9]+s[0-9]+, Data disk[0-9]+s[0-9]+, booted com\.apple\.bless\.' \
        '^mounted /dev/disk[0-9]+s[0-9]+ read-write at /private/var/run/hao-live-system$' \
        '^named snapshot hao-pre-hao-[0-9-]+ taken before writing$' '^next boot: new snapshot com\.apple\.bless\.' \
        '^applied hao-[0-9-]+: 3 written, 0 attributes set, 0 unchanged, snapshot com\.apple\.bless\.' \
        '^hao-[0-9-]+ +applied +3 files' 'No such file or directory' 'data-probe.txt' ;;
reboot-note)
    cat <<EOF
Reboot (the lock holder's step): the laptop's default boot is the NetBoot Recovery, so
  1. $ssh_tool -- shutdown -r now                    # back to the Recovery
  2. ~/mac-image/netboot/bench IMAGE_REPO --no-push --stay   # one-time boot of the internal disk, keep it there
Then: $0 verify-applied (after apply) or $0 verify-reverted (after revert).
EOF
    exit 0 ;;
verify-applied)
    on "$W/python/bin/python3 -I $W/hao/laptop/hao9a.py verify applied $OV; $HAO --probe" | tee "$log" || true
    # PASS for the three files (after sha256) and their mode/owner/flags, the record applied, running its snapshot.
    # NOTE lines answer the open question: did snapshot_booted and snapshot_pre SURVIVE a boot?
    expect "$log" '^applied: 0 failed$' '^NOTE snapshot_booted .* (SURVIVED|is GONE after) this boot$' \
        '^ok   no hao patch or revert since this boot' '^live patch would run$' ;;
revert)
    on "$HAO --revert --live --i-have-a-backup --show" | tee "$log"
    on "ls '/Library/Application Support/hao-9a' 2>&1; diskutil apfs listSnapshots /" | tee -a "$log"
    # mounted ...; remove/remove/restore lines; next boot: snapshot com.apple.bless.<A>;
    # reverted hao-<ID>: 3 files; reboot to run snapshot com.apple.bless.<A>   (A = the snapshot booted before apply)
    expect "$log" '^next boot: snapshot ' '^reverted hao-[0-9-]+: 3 files; reboot to run snapshot ' \
        '^  restore usr/share/misc/' 'No such file or directory' ;;
verify-reverted)
    on "$W/python/bin/python3 -I $W/hao/laptop/hao9a.py verify reverted $OV; $HAO --probe" | tee "$log" || true
    expect "$log" '^reverted: 0 failed$' '^PASS that is the snapshot booted before the patch$' \
        '^live patch would run$' ;;
collect)
    on "cd /private/var/db && tar -cf - hao" > "$evidence/state-hao.tar"
    on "diskutil info -plist /; diskutil apfs listSnapshots -plist /; mount" > "$evidence/disks.txt"
    echo "evidence in $evidence"; ls -l "$evidence" ;;
*) die "unknown step $step" ;;
esac
