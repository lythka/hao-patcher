#!/bin/sh
# hao-accept job, guest part 1: runs as root in the USB installer's Recovery (typed by hao-accept through QMP), from the
# test disk HAOACCEPT. Writes the TEST-ONLY job (jobwrap.sh, the given job.sh and its payload/) into the Data volume of
# the installed disk already attached (never the USB installer, never a release), then powers off.
# GC.md "Store integrity": no .pyc beside a USB installer's or the store's Python files.
export PYTHONDONTWRITEBYTECODE=1
T=/Volumes/HAOACCEPT
say() { echo "HAOACCEPT $*" > /dev/console; }
say "BEGIN arm $(date -u +%s)"
D= ; best=0
for d in $(diskutil list internal physical | awk '/^\/dev\/disk/{sub("/dev/","",$1); print $1}'); do
  b=$(diskutil info "$d" | sed -n 's/.*Disk Size:.*(\([0-9]*\) Bytes).*/\1/p')
  [ -n "$b" ] && [ "$b" -gt 60000000000 ] && [ "$b" -gt "$best" ] && { D=$d; best=$b; }
done
C=$(diskutil apfs list | awk -v s="${D}s2" '/APFS Container Reference:/ {c=$NF} /APFS Physical Store Disk:/ && $NF==s {print c; exit}')
DATAD=$(diskutil apfs list "$C" 2>/dev/null | awk '/APFS Volume Disk \(Role\):/ && /\(Data\)/ {print $(NF-1); exit}')
diskutil mount "$DATAD" > /dev/null 2>&1
DATA=$(diskutil info "$DATAD" | sed -n 's/^ *Mount Point: *//p')
if [ -z "$DATA" ] || [ ! -d "$DATA/Library" ]; then say "CHECK arm FAIL no Data volume on $D ($C $DATAD)"; say "END arm"; shutdown -h now; exit 1; fi
diskutil enableOwnership "$DATAD" > /dev/null 2>&1
H="$DATA/Library/hao-accept"
rm -rf "$H"; mkdir -p "$H" "$DATA/Library/LaunchDaemons"
cp "$T/jobwrap.sh" "$T/job.sh" "$H/" && cp -R "$T/payload" "$H/payload" 2>/dev/null
cp "$T/dev.hao.job.plist" "$DATA/Library/LaunchDaemons/dev.hao.job.plist"
# A disk from a hao-accept run may still carry that run's own job: only this job runs.
rm -f "$DATA/Library/LaunchDaemons/dev.hao.accept.plist"
# payload/arm-hook.sh, when given, runs here (root, the Recovery) with H and DATA, e.g. to copy files from the installer.
[ -f "$T/payload/arm-hook.sh" ] && { H="$H" DATA="$DATA" sh "$T/payload/arm-hook.sh" && say "ARM-HOOK ok" || say "ARM-HOOK rc $?"; }
chown -R 0:0 "$H" "$DATA/Library/LaunchDaemons/dev.hao.job.plist"
chmod 755 "$H/jobwrap.sh" "$H/job.sh"; chmod 644 "$DATA/Library/LaunchDaemons/dev.hao.job.plist"
if [ -f "$DATA/Library/LaunchDaemons/dev.hao.job.plist" ] && [ -f "$H/job.sh" ]; then say "CHECK arm PASS job armed in $DATAD ($DATA)"
else say "CHECK arm FAIL job not written"; fi
say "END arm $(date -u +%s)"
sync; diskutil unmount "$DATA" > /dev/null 2>&1; diskutil unmount "$T" > /dev/null 2>&1
shutdown -h now
