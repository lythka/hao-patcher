#!/bin/sh
# hao-accept, guest part 2: a TEST-ONLY launchd job (dev.hao.accept) that recovery.sh writes into the QEMU guest's
# installed Data volume. It is never on the USB installer or in a release. At the installed system's first boot it
# measures boot milestones, checks a login shell, su and sudo, removes itself and its test user, and powers off.
# Result lines start with "HAOACCEPT " and go to the console (serial) and, when the HAOACCEPT disk mounts, to
# HAOACCEPT/out/boot.txt.
# GC.md "Store integrity": no .pyc beside a USB installer's or the store's Python files.
export PYTHONDONTWRITEBYTECODE=1
LOG=/Library/hao-accept/boot.txt
: > "$LOG"
say() { echo "HAOACCEPT $*" | tee -a "$LOG" > /dev/console; }
check() { say "CHECK $1 $2 ${3:-}"; }
boot=$(sysctl -n kern.boottime | sed -n 's/.*sec = \([0-9]*\),.*/\1/p')
since() { echo $(( $(date -u +%s) - boot )); }
say "BEGIN boot $(date -u +%s) uptime $(since)"
# Harness setting: no sleep for the whole job (display, idle, disk, system), so a sleep/wake fault (diag #79, a
# USB EHCI wake panic) cannot fail an acceptance run; sleep/wake is tested on its own.
caffeinate -dims -w $$ > /dev/null 2>&1 &
say "INFO caffeinate -dims for the job (harness setting)"
check boot PASS "the installed system ran the first-boot job at +$(since) s"
# On a laptop (hao-accept laptop, file REBOOT) the job ends with a restart, which returns the laptop to its NetBoot
# Recovery (the firmware default), and a watchdog restarts it after 40 min whatever happens; in QEMU it powers off.
END_CMD="shutdown -h now"
if [ -f /Library/hao-accept/STAY ]; then
  # The dev build's one boot (owner: "Minimize boots"): no restart here; hao-check follows over SSH in this same boot and
  # bench restarts once at the end. The watchdog is the backstop for a boot nobody reaches: 150 min.
  END_CMD=":"
  ( sleep 9000; echo "HAOACCEPT WATCHDOG restart after 150 min" >> "$LOG"; sync; shutdown -r now ) &
elif [ -f /Library/hao-accept/REBOOT ]; then
  END_CMD="shutdown -r now"
  ( sleep 2400; echo "HAOACCEPT WATCHDOG restart after 40 min" >> "$LOG"; sync; shutdown -r now ) &
fi

# Milestones: WindowServer and loginwindow (a fresh install shows Setup Assistant from loginwindow). Up to 20 min.
ws= ; lw=
i=0
while [ $i -lt 600 ]; do
  [ -z "$ws" ] && pgrep -x WindowServer > /dev/null && ws=$(since)
  [ -z "$lw" ] && pgrep -x loginwindow > /dev/null && lw=$(since)
  [ -n "$ws" ] && [ -n "$lw" ] && break
  sleep 2; i=$((i + 1))
done
say "TIME boot-windowserver ${ws:-none}"
say "TIME boot-loginwindow ${lw:-none}"
if [ -n "$lw" ]; then check loginwindow PASS "+$lw s after boot"; else check loginwindow FAIL "no loginwindow in 20 min"; fi
sleep 20
pgrep -lf "Setup Assistant" > /dev/null && say "INFO Setup Assistant running"

# Set-ID modes as the running system sees them.
for f in /usr/bin/login:4555 /usr/bin/sudo:4511 /usr/bin/su:4755; do
  p=${f%%:*}; want=${f#*:}
  got=$(stat -f '%Mp%Lp %u' "$p" 2>/dev/null)
  if [ "$got" = "$want 0" ]; then check "live-setid:$p" PASS "$got"; else check "live-setid:$p" FAIL "want $want 0, got '$got'"; fi
done

# hao verify / on the running system, with the installer's hao and Python that recovery.sh staged (removed below).
H=/Library/hao-accept
if [ -x "$H/py/bin/python3.12" ] && [ -f "$H/hao/tools/hao/hao" ]; then
  t0=$(date -u +%s)
  "$H/py/bin/python3.12" -I "$H/hao/tools/hao/hao" verify / > "$H/verify.txt" 2>&1; rc=$?
  tail -15 "$H/verify.txt" | sed 's/^/HAOACCEPT VERIFY| /' | tee -a "$LOG" > /dev/console
  if [ $rc -eq 0 ]; then check hao-verify PASS "hao verify / rc 0, $(( $(date -u +%s) - t0 )) s"; else check hao-verify FAIL "hao verify / rc $rc"; fi
else check hao-verify FAIL "hao and python were not staged in $H"; fi
rm -rf "$H/hao" "$H/py"

# A non-admin test user, removed at the end.
U=haoaccept
dscl . -create /Users/$U && dscl . -create /Users/$U UniqueID 599 && dscl . -create /Users/$U PrimaryGroupID 20 &&
  dscl . -create /Users/$U UserShell /bin/zsh && dscl . -create /Users/$U NFSHomeDirectory /private/var/tmp/$U
mkdir -p /private/var/tmp/$U && chown 599:20 /private/var/tmp/$U
if id $U > /dev/null 2>&1; then check user PASS "test user $U (uid 599)"; else check user FAIL "dscl could not create $U"; fi

# Through /usr/bin/login (set-ID root) in a pty, with the program given to login: zsh's line editor swallowed commands
# piped into an interactive login shell (QEMU 0d7818f9 try 2: the prompt came up as the user, the echo never ran).
out=$(script -q /dev/null /usr/bin/login -f $U /bin/sh -c 'echo LOGINOK-$(id -un)-login' < /dev/null 2>&1 | tr -d '\r')
case $out in *LOGINOK-$U-*) check login-shell PASS "$(echo "$out" | grep -o "LOGINOK-[^ ]*" | head -1)" ;;
  *) check login-shell FAIL "$(echo "$out" | tail -3 | tr '\n' ' ')" ;; esac

# su -l to the test user (login shell, profile read).
out=$(su -l $U -c 'echo SUOK-$(id -un)' 2>&1)
case $out in *SUOK-$U*) check su-login PASS "su -l ran as $U" ;; *) check su-login FAIL "$(echo "$out" | tail -2 | tr '\n' ' ')" ;; esac

# sudo: the policy loads for root (sudo -n true, rc 0) and, for a non-admin user, sudo runs set-ID and asks for a
# password (rc 1, "password is required"), not "must be owned by uid 0 and have the setuid bit set".
sudo -n true > /dev/null 2>&1; rc=$?
if [ $rc -eq 0 ]; then check sudo-root PASS "sudo -n true rc 0"; else check sudo-root FAIL "sudo -n true rc $rc"; fi
out=$(su $U -c 'sudo -n true' 2>&1); rc=$?
case $out in
  *setuid*|*"uid 0"*) check sudo-user FAIL "$out" ;;
  *"password is required"*) check sudo-user PASS "rc $rc, policy asks for a password" ;;
  *) check sudo-user FAIL "rc $rc: $out" ;;
esac

# Clean up: the test user and this job.
dscl . -delete /Users/$U > /dev/null 2>&1; rm -rf /private/var/tmp/$U
rm -f /Library/LaunchDaemons/dev.hao.accept.plist
if [ -e /Library/LaunchDaemons/dev.hao.accept.plist ] || id $U > /dev/null 2>&1; then check cleanup FAIL "job or user left"
else check cleanup PASS "test user and job removed (boot.sh and its log stay in /Library/hao-accept)"; fi

# Copy the log to the HAOACCEPT disk, if it mounted.
v=$(diskutil list | awk '/HAOACCEPT/{print $NF; exit}')
[ -n "$v" ] && diskutil mount "$v" > /dev/null 2>&1
m=$(diskutil info "$v" 2>/dev/null | sed -n 's/^ *Mount Point: *//p')
say "END boot $(date -u +%s)"
if [ -n "$m" ] && [ -d "$m" ]; then mkdir -p "$m/out"; cp "$LOG" "$m/out/boot.txt"; sync; diskutil unmount "$m" > /dev/null 2>&1; fi
sync
$END_CMD
