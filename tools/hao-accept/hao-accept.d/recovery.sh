#!/bin/sh
# hao-accept, guest part 1: runs as root in the USB installer's Recovery (typed by hao-accept through QMP), from the
# test disk HAOACCEPT, never from the USB installer. Installs onto the empty internal disk with the installer's own
# command, checks the installed disk, writes the test-only first-boot job into its Data volume, then powers off.
# Every result line starts with "HAOACCEPT " and goes to the console (serial) and to HAOACCEPT/out/recovery.txt.
# hao-accept laptop runs it over SSH in the NetBoot Recovery instead: HAOACCEPT_DIR names the folder that holds these
# files, and the file LAPTOP there means: no power-off at the end, and the first boot restarts instead of powering off.
# GC.md "Store integrity": no .pyc beside a USB installer's or the store's Python files.
export PYTHONDONTWRITEBYTECODE=1
T=${HAOACCEPT_DIR:-/Volumes/HAOACCEPT}
OUT=$T/out
mkdir -p "$OUT"
LOG=$OUT/recovery.txt
: > "$LOG"
say() { echo "HAOACCEPT $*" | tee -a "$LOG" > /dev/console; }
check() { say "CHECK $1 $2 ${3:-}"; }   # name PASS|FAIL detail
now() { date -u +%s; }
HAO=${HAO_DIR:-/Volumes/HAO}   # hao-accept laptop passes the installer volume's real mount point
PY=$HAO/kit/py-x86_64/bin/python3.12
say "BEGIN recovery $(now)"

# The installed system is checked only when its System volume mounted (SYS non-empty), never this Recovery.
# The target: the largest internal physical disk over 60 GB (the QEMU ESP disk is small; the USB disks are external).
D= ; best=0
for d in $(diskutil list internal physical | awk '/^\/dev\/disk/{sub("/dev/","",$1); print $1}'); do
  b=$(diskutil info "$d" | sed -n 's/.*Disk Size:.*(\([0-9]*\) Bytes).*/\1/p')
  [ -n "$b" ] && [ "$b" -gt 60000000000 ] && [ "$b" -gt "$best" ] && { D=$d; best=$b; }
done
if [ -z "$D" ]; then check target FAIL "no internal disk over 60 GB"; say "END recovery $(now)"; shutdown -h now; exit 1; fi
say "TARGET $D $best"

# 1. Install with the installer's own launcher (it adds --kit and --assembly); the confirmation is the disk's name.
t0=$(now)
# stdin /dev/null: in the QEMU run this script runs under script(1), a pty, and install would restart after 10 s.
if [ -n "${INSTALL_RESUME:-}" ]; then   # continue an install that stopped, at its own --resume (hao-accept --install-resume)
  "$HAO/install" "/dev/$D" --model MacBookPro9,2 --resume "$INSTALL_RESUME" --confirm RESUME > "$OUT/install.txt" 2>&1 < /dev/null
else
  "$HAO/install" "/dev/$D" --model MacBookPro9,2 --confirm ERASE > "$OUT/install.txt" 2>&1 < /dev/null
fi
rc=$?
t1=$(now)
tail -40 "$OUT/install.txt" | sed 's/^/HAOACCEPT INSTALL| /' > /dev/console
say "TIME install $((t1 - t0))"
if [ $rc -eq 0 ]; then check install PASS "rc 0, $((t1 - t0)) s"; else check install FAIL "rc $rc"; fi

# The installed container and its System and Data volumes.
C=$(diskutil apfs list | awk -v s="${D}s2" '
  /APFS Container Reference:/ {c=$NF} /APFS Physical Store Disk:/ && $NF==s {print c; exit}')
SYSD=$(diskutil apfs list "$C" 2>/dev/null | awk '/APFS Volume Disk \(Role\):/ && /\(System\)/ {print $(NF-1); exit}')
DATAD=$(diskutil apfs list "$C" 2>/dev/null | awk '/APFS Volume Disk \(Role\):/ && /\(Data\)/ {print $(NF-1); exit}')
say "VOLUMES container=$C system=$SYSD data=$DATAD"
mp() { diskutil info "$1" | sed -n 's/^ *Mount Point: *//p'; }
[ -n "$SYSD" ] && diskutil mount "$SYSD" > /dev/null 2>&1
[ -n "$DATAD" ] && diskutil mount "$DATAD" > /dev/null 2>&1
SYS=$(mp "$SYSD" 2>/dev/null); DATA=$(mp "$DATAD" 2>/dev/null)
if [ -d "$SYS/usr/bin" ] && [ -d "$DATA" ]; then check volumes PASS "$SYS | $DATA"; else check volumes FAIL "system '$SYS' data '$DATA'"; fi

# Demo only (hao-accept --break-setid): clear one set-ID bit on the installed system before the checks.
if [ -f "$T/BREAK-SETID" ] && [ -n "$SYS" ] && [ -d "$SYS/usr/bin" ]; then
  p=$(cat "$T/BREAK-SETID")
  mount -uw "$SYS" 2>/dev/null
  if chmod u-s "$SYS/$p" 2>/dev/null; then say "DEMO broke set-ID on $p"; else say "DEMO could not clear set-ID on $p"; fi
fi

# 2. Set-ID modes. Fixed: login 4555, sudo 4511, su 4755, owner root. Then every set-ID file of the reference
# (HAOACCEPT/setid-reference.txt: "mode uid gid path" from a stock macOS 27, written by hao-accept on its host) that
# exists on the installed system must have the same mode and owner; set-ID files of the installed system that are not
# in the reference are listed, not judged. (The Recovery's BaseSystem is no reference: its login is 0555.)
if [ -n "$SYS" ] && [ -d "$SYS/usr/bin" ]; then
  for f in usr/bin/login:4555 usr/bin/sudo:4511 usr/bin/su:4755; do
    p=${f%%:*}; want=${f#*:}
    got=$(stat -f '%Mp%Lp %u' "$SYS/$p" 2>/dev/null)
    if [ "$got" = "$want 0" ]; then check "setid:$p" PASS "$got"; else check "setid:$p" FAIL "want $want 0, got '$got'"; fi
  done
  (cd "$SYS" && find . -xdev -type f \( -perm -4000 -o -perm -2000 \) 2>/dev/null | sed 's|^\./||' | sort) > "$OUT/setid-installed.txt"
  bad=0; n=0; other=0
  while read -r mode uid gid p; do
    [ -e "$SYS/$p" ] || continue
    a=$(stat -f '%Mp%Lp %u %g' "$SYS/$p"); n=$((n + 1))
    if [ "$a" != "$mode $uid $gid" ]; then bad=$((bad + 1)); say "SETID-DIFF $p installed=$a reference=$mode $uid $gid"; fi
  done < "$T/setid-reference.txt"
  for p in $(cat "$OUT/setid-installed.txt"); do
    grep -q " $p\$" "$T/setid-reference.txt" || { other=$((other + 1)); say "SETID-UNCOMPARED $p installed=$(stat -f '%Mp%Lp %u %g' "$SYS/$p")"; }
  done
  if [ $bad -eq 0 ] && [ $n -gt 0 ]; then check setid:all PASS "$n reference files match, $other set-ID files not in the reference"
  else check setid:all FAIL "$bad of $n differ from the reference"; fi
fi

# 3. hao verify runs on the booted system (hao refuses verify on the USB installer: frontdoor.where() is "installer"
# for a hao beside the kit, exit 5). The installed release has no hao command, so, as a user would run it from a copy,
# the installer's hao and its x86_64 Python go into the test-only /Library/hao-accept; boot.sh runs `hao verify /` and
# removes them.
if [ -n "$DATA" ] && [ -d "$DATA" ]; then
  mkdir -p "$DATA/Library/hao-accept"
  if ditto "$HAO/hao" "$DATA/Library/hao-accept/hao" && ditto "$HAO/kit/py-x86_64" "$DATA/Library/hao-accept/py"; then
    say "STAGED hao and python for hao verify at first boot"
  else check hao-verify FAIL "could not copy hao and python into the installed Data volume"; fi
fi

# 4. The documented Recovery command (docs/install.md, tools/hao/README.md): the installer's report.
"$HAO/report" > "$OUT/report-cmd.txt" 2>&1
rc=$?
if [ $rc -eq 0 ]; then check "docs:/Volumes/HAO/report" PASS "rc 0"; else check "docs:/Volumes/HAO/report" FAIL "rc $rc"; fi

# 5. The test-only first-boot job, into the installed Data volume (never the USB installer, never a release).
if [ -n "$DATA" ] && [ -d "$DATA" ]; then
  diskutil enableOwnership "$DATA" > /dev/null 2>&1
  mkdir -p "$DATA/Library/LaunchDaemons" "$DATA/Library/hao-accept"
  cp "$T/boot.sh" "$DATA/Library/hao-accept/boot.sh"
  cp "$T/dev.hao.accept.plist" "$DATA/Library/LaunchDaemons/dev.hao.accept.plist"
  chown -R 0:0 "$DATA/Library/hao-accept" "$DATA/Library/LaunchDaemons/dev.hao.accept.plist"
  chmod 755 "$DATA/Library/hao-accept/boot.sh"; chmod 644 "$DATA/Library/LaunchDaemons/dev.hao.accept.plist"
  [ -f "$T/LAPTOP" ] && touch "$DATA/Library/hao-accept/REBOOT"
  [ -f "$T/STAY" ] && touch "$DATA/Library/hao-accept/STAY"
  say "ARMED test-only first-boot job dev.hao.accept in the installed Data volume"
fi

# 6. fsck of the installed container, unmounted, read-only.
diskutil unmountDisk force "$C" > /dev/null 2>&1
diskutil unmountDisk force "$D" > /dev/null 2>&1
fsck_apfs -n "/dev/r${D}s2" > "$OUT/fsck.txt" 2>&1
rc=$?
tail -8 "$OUT/fsck.txt" | sed 's/^/HAOACCEPT FSCK| /' > /dev/console
if [ $rc -eq 0 ]; then check fsck PASS "fsck_apfs -n rc 0"; else check fsck FAIL "fsck_apfs -n rc $rc"; fi

say "END recovery $(now)"
sync
[ -f "$T/LAPTOP" ] && exit 0
diskutil unmount "$T" > /dev/null 2>&1
shutdown -h now
