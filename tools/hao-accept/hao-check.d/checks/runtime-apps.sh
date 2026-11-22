#!/bin/sh
# hao-check plug-in, runtime: translated programs, Swift value boundaries, AOT state, floating point, log hygiene.
# Run as root over SSH on the laptop's dev image (POSIX sh), with OUT, CUSER, CUID set (see checks/README).
# Optional: KIT = a folder holding fma64.arm64e and fma64.expected (the FMA kit); without it RT4 is SKIP.
# Prints "HAOCHECK CHECK <name> <PASS|FAIL|SKIP> <detail> [issue=#N]" lines, then "HAOCHECK END runtime-apps".
# Changes nothing: it launches and quits apps as the console user and writes only under $OUT.

PATH=/usr/bin:/bin:/usr/sbin:/sbin:/usr/libexec
export PATH
LC_ALL=C
export LC_ALL

if [ -z "${OUT:-}" ]; then echo "HAOCHECK CHECK env FAIL OUT is not set"; echo "HAOCHECK END runtime-apps"; exit 2; fi
mkdir -p "$OUT/runtime"
if [ -z "${CUSER:-}" ]; then CUSER=$(stat -f %Su /dev/console 2> /dev/null); fi
if [ -z "${CUID:-}" ] && [ -n "$CUSER" ]; then CUID=$(id -u "$CUSER" 2> /dev/null); fi
R="$OUT/runtime"

say() { echo "HAOCHECK CHECK $*"; }

# bounded SECS CMD...: run CMD, kill it after SECS seconds; returns its status, or 124 on timeout.
bounded() {
  _s=$1; shift
  "$@" &
  _p=$!
  ( sleep "$_s"; kill -KILL "$_p" 2> /dev/null ) > /dev/null 2>&1 < /dev/null &
  _w=$!
  wait "$_p"; _rc=$?
  kill "$_w" 2> /dev/null; wait "$_w" 2> /dev/null
  [ $_rc -eq 137 ] && return 124
  return $_rc
}
as_user() { launchctl asuser "$CUID" sudo -u "$CUSER" "$@"; }

hao_logs() { ls /private/var/log/hao/console.log /private/var/log/hao/*/console.log 2> /dev/null; }
refusals() {   # refusal lines in the hao logs, optionally for one pid
  _f=$(hao_logs); [ -z "$_f" ] && return 0
  if [ -n "${1:-}" ]; then grep -h -E "(Swift value boundary refused|bridge: REFUSED)" $_f 2> /dev/null | grep -F "[$1:$2]"; \
  else grep -h -E "(Swift value boundary refused|bridge: REFUSED)" $_f 2> /dev/null; fi
}
crash_count() {   # crash reports naming process $1
  _n=0
  for d in /Library/Logs/DiagnosticReports /Users/$CUSER/Library/Logs/DiagnosticReports; do
    [ -d "$d" ] || continue
    _c=$(ls "$d" 2> /dev/null | grep -c "^$1[-_.]")
    _n=$((_n + _c))
  done
  echo $_n
}

# RT2: the apps behind the known Swift value boundaries. Launch as the console user, 20 s up, still running, no new
# crash report, no refusal line for its pid; then quit. Interactive steps (drag a font, hover Sound, add a widget)
# are not automated here: bench's manual pass covers them.
app_check() {   # name app-name process-name [issue]
  _name=$1; _app=$2; _proc=$3; _iss=${4:-}
  _c0=$(crash_count "$_proc")
  bounded 20 as_user open -a "$_app" > "$R/$_name.open.txt" 2>&1
  sleep 20
  _pid=$(pgrep -x "$_proc" | head -1)
  _c1=$(crash_count "$_proc")
  _ref=""
  [ -n "$_pid" ] && _ref=$(refusals "$_proc" "$_pid" | head -1)
  refusals > "$R/$_name.refusals.txt"
  bounded 10 as_user osascript -e "tell application \"$_app\" to quit" > /dev/null 2>&1
  if [ -z "$_pid" ]; then say "$_name FAIL not running 20 s after launch (crash reports $_c0 -> $_c1)" $_iss
  elif [ "$_c1" -gt "$_c0" ]; then say "$_name FAIL new crash report" $_iss
  elif [ -n "$_ref" ]; then say "$_name FAIL $(echo "$_ref" | cut -c1-160)" $_iss
  else say "$_name PASS up 20 s, no crash report, no refusal" $_iss; fi
}

app_check rt2-fontbook "Font Book" "Font Book"
app_check rt2-tips "Tips" "Tips"
app_check rt2-stocks "Stocks" "Stocks"
app_check rt2-weather "Weather" "Weather" issue=#62
# Notification Center: no app to open; check the process survives and logged no refusal.
_nc=$(pgrep -x NotificationCenter | head -1)
if [ -z "$_nc" ]; then say rt2-notificationcenter FAIL "NotificationCenter not running" issue=#59
elif [ -n "$(refusals NotificationCenter "$_nc" | head -1)" ]; then say rt2-notificationcenter FAIL "refusal logged" issue=#59
else say rt2-notificationcenter PASS "running, no refusal" issue=#59; fi
_cc=$(pgrep -x ControlCenter | head -1)
if [ -z "$_cc" ]; then say rt2-controlcenter FAIL "ControlCenter not running"
elif [ -n "$(refusals ControlCenter "$_cc" | head -1)" ]; then say rt2-controlcenter FAIL "refusal logged"
else say rt2-controlcenter PASS "running, no refusal"; fi

# RT1: refusal lines anywhere in the hao logs (all translated programs, after bench's app sweep if it ran first).
refusals > "$R/rt1-refusals.txt"
_n=$(wc -l < "$R/rt1-refusals.txt" | tr -d ' ')
if [ "$_n" -eq 0 ]; then say rt1-refusals PASS "no Swift value or bridge refusal in the hao logs"
else say rt1-refusals FAIL "$_n refusal lines (see runtime/rt1-refusals.txt): $(head -1 "$R/rt1-refusals.txt" | cut -c1-120)"; fi

# RT3: AOT state after the first-login job.
bounded 300 /usr/libexec/hao --aot --check > "$R/rt3-aot-check.txt" 2>&1; _rc=$?
if [ $_rc -eq 0 ]; then say rt3-aot-check PASS "all listed caches valid"
elif [ $_rc -eq 124 ]; then say rt3-aot-check FAIL "hao --aot --check timed out"
else say rt3-aot-check FAIL "exit $_rc (3 missing, 4 stale): $(tail -1 "$R/rt3-aot-check.txt" | cut -c1-120)"; fi
if [ -f /private/var/db/hao/auto-aot.done ]; then say rt3-aot-marker PASS "$(head -1 /private/var/db/hao/auto-aot.done | cut -c1-80)"
else say rt3-aot-marker FAIL "no /private/var/db/hao/auto-aot.done"; fi
du -sh /usr/lib/hao/aot /private/var/db/hao > "$R/rt3-aot-size.txt" 2>&1

# RT4: double-precision fused multiply-add exactness (also a translated command-line run).
if [ -n "${KIT:-}" ] && [ -x "$KIT/fma64.arm64e" ] && [ -f "$KIT/fma64.expected" ]; then
  bounded 120 "$KIT/fma64.arm64e" > "$R/rt4-fma64.txt" 2>&1; _rc=$?
  if [ $_rc -eq 0 ] && cmp -s "$R/rt4-fma64.txt" "$KIT/fma64.expected"; then say rt4-fma64 PASS "identical to native arm64"
  else say rt4-fma64 FAIL "rc $_rc or output differs from native (runtime/rt4-fma64.txt)"; fi
else say rt4-fma64 SKIP "KIT not given"; fi

# RT6: log hygiene; writable+executable memory in a translated app without its own JIT (informational).
_m=$(stat -f %Lp /private/var/log/hao 2> /dev/null); _c=$(stat -f %Lp /private/var/log/hao/console.log 2> /dev/null)
if [ "$_m" = 755 ] && { [ -z "$_c" ] || [ "$_c" = 600 ]; }; then say rt6-logperms PASS "dir $_m console.log ${_c:-absent}"
else say rt6-logperms FAIL "dir ${_m:-absent} console.log ${_c:-absent} (want 755/600)"; fi
bounded 20 as_user open -a Calculator > /dev/null 2>&1; sleep 5
_p=$(pgrep -x Calculator | head -1)
if [ -n "$_p" ]; then
  bounded 30 vmmap "$_p" > "$R/rt6-vmmap.txt" 2>&1
  _w=$(grep -c 'rwx/rwx' "$R/rt6-vmmap.txt")
  say rt6-wx PASS "informational: $_w rwx/rwx regions in Calculator (0 expected on Intel)"
  bounded 10 as_user osascript -e 'tell application "Calculator" to quit' > /dev/null 2>&1
else say rt6-wx SKIP "Calculator did not start"; fi

echo "HAOCHECK END runtime-apps"
