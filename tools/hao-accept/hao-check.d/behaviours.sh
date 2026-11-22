#!/bin/sh
# hao-check, behaviours: TEST-ONLY checks of a running hao macOS 27 image on the MacBookPro9,2 test laptop. It is
# never on a USB installer or in a release; the bench orchestrator copies it to the laptop and runs it there.
#
# Run as root over SSH on the laptop (POSIX sh; macOS /bin/sh), with:
#   OUT    a writable directory for evidence files ($OUT/<check>.txt)
#   CUSER  the console user's name (the test user)
#   CUID   that user's uid (e.g. 501)
#   e.g.  OUT=/var/tmp/hao-check CUSER=tester CUID=501 sh behaviours.sh
#
# Output: one line per check on stdout, then an end line:
#   HAOCHECK CHECK <name> <PASS|FAIL|SKIP> <one-line detail> [issue=#N]
#   HAOCHECK END behaviours
# Everything else (raw command output) goes to $OUT, never to stdout.
#
# It changes nothing on the system: no network joins, no pairing, no launchctl load/unload, no pmset changes, no
# reboots; it writes only under $OUT (and plays one short system sound). Every command that can block is bounded.
# Sleep/wake is not here (the orchestrator runs it last, on its own).

PATH=/usr/bin:/bin:/usr/sbin:/sbin:/usr/libexec:/usr/local/bin
export PATH
LC_ALL=C
export LC_ALL

if [ -z "${OUT:-}" ]; then
  echo "HAOCHECK CHECK env FAIL OUT is not set"
  echo "HAOCHECK END behaviours"
  exit 2
fi
mkdir -p "$OUT" 2> /dev/null
if [ -z "${CUSER:-}" ]; then CUSER=$(stat -f %Su /dev/console 2> /dev/null); fi
if [ -z "${CUID:-}" ] && [ -n "$CUSER" ]; then CUID=$(id -u "$CUSER" 2> /dev/null); fi
TMPD="$OUT/.behaviours.tmp"
mkdir -p "$TMPD"

# --- helpers ---------------------------------------------------------------------------------------------------

# bounded SECS CMD...: run CMD, kill it (TERM, then KILL) after SECS seconds. Returns CMD's status, or 124 on timeout.
# The watcher's output goes to /dev/null so a $(bounded ...) capture never waits for the watcher's sleep.
BN=0
bounded() {
  _secs=$1; shift
  BN=$((BN + 1))
  _mark="$TMPD/timeout.$$.$BN"
  rm -f "$_mark"
  "$@" &
  _pid=$!
  ( sleep "$_secs"
    if kill -0 "$_pid" 2> /dev/null; then
      : > "$_mark"; kill -TERM "$_pid" 2> /dev/null; sleep 2; kill -KILL "$_pid" 2> /dev/null
    fi ) > /dev/null 2>&1 < /dev/null &
  _w=$!
  wait "$_pid"
  _rc=$?
  pkill -P "$_w" 2> /dev/null
  kill "$_w" 2> /dev/null
  wait "$_w" 2> /dev/null
  if [ -f "$_mark" ]; then rm -f "$_mark"; return 124; fi
  return "$_rc"
}

# as_user CMD...: run CMD as the console user in the console user's GUI (Aqua) session.
as_user() { launchctl asuser "$CUID" sudo -u "$CUSER" "$@"; }

have_user() { [ -n "$CUSER" ] && [ -n "$CUID" ] && [ "$CUSER" != root ] && [ "$CUID" != 0 ]; }

oneline() { tr '\n\t' '  ' | tr -s ' ' | sed 's/^ //; s/ $//' | cut -c1-400; }

RESULTED=0
result() {
  # result NAME STATUS DETAIL [ISSUE]
  _d=$(printf '%s' "$3" | oneline)
  if [ -n "${4:-}" ]; then
    printf 'HAOCHECK CHECK %s %s %s issue=%s\n' "$1" "$2" "$_d" "$4"
  else
    printf 'HAOCHECK CHECK %s %s %s\n' "$1" "$2" "$_d"
  fi
  RESULTED=1
}

# run_check NAME FUNC: run one check; a check that prints no result line is reported as FAIL.
run_check() {
  RESULTED=0
  "$2"
  [ "$RESULTED" = 1 ] || result "$1" FAIL "the check printed no result (script error)"
}

BOOT=$(sysctl -n kern.boottime 2> /dev/null | sed -n 's/.*sec = \([0-9]*\),.*/\1/p')

# lstart_epoch PID: a process's start time as epoch seconds (empty if unknown).
lstart_epoch() {
  _ls=$(ps -o lstart= -p "$1" 2> /dev/null | tr -s ' ' | sed 's/^ //; s/ $//')
  [ -n "$_ls" ] || return 0
  date -j -f '%a %b %d %T %Y' "$_ls" +%s 2> /dev/null
}

# --- checks ----------------------------------------------------------------------------------------------------

check_wifi() {
  f="$OUT/wifi.txt"
  bounded 15 networksetup -listallhardwareports > "$TMPD/ports" 2>&1
  dev=$(awk '/^Hardware Port: (Wi-Fi|AirPort)$/ { getline; sub(/^Device: /, ""); print; exit }' "$TMPD/ports")
  { echo "== networksetup -listallhardwareports"; cat "$TMPD/ports"; } > "$f"
  if [ -z "$dev" ]; then result wifi FAIL "no Wi-Fi hardware port"; return; fi
  pw=$(bounded 15 networksetup -getairportpower "$dev" 2>&1)
  { echo "== networksetup -getairportpower $dev"; echo "$pw"; } >> "$f"
  bounded 60 system_profiler SPAirPortDataType > "$TMPD/airport" 2>&1
  sprc=$?
  { echo "== system_profiler SPAirPortDataType (rc $sprc)"; cat "$TMPD/airport"; } >> "$f"
  card=$(grep -m1 'Card Type:' "$TMPD/airport" | sed 's/.*Card Type: *//')
  status=$(grep -m1 'Status:' "$TMPD/airport" | sed 's/.*Status: *//')
  # Each network listed (current and other local) carries one "PHY Mode:" line; "Supported PHY Modes" does not match.
  nets=$(grep -c '^ *PHY Mode:' "$TMPD/airport")
  ip=$(bounded 10 ipconfig getifaddr "$dev" 2> /dev/null)
  { echo "== ipconfig getifaddr $dev"; echo "${ip:-none}"; } >> "$f"
  case "$pw" in *": On"*) on=1 ;; *) on=0 ;; esac
  d="dev $dev power $(echo "$pw" | sed 's/.*: //'), card ${card:-unknown}, status ${status:-unknown}, networks seen $nets, ip ${ip:-none}"
  [ "$sprc" = 124 ] && d="$d, system_profiler timed out (60 s)"
  if [ "$on" = 1 ] && [ "$nets" -ge 1 ]; then result wifi PASS "$d"; else result wifi FAIL "$d"; fi
}

check_bluetooth() {
  f="$OUT/bluetooth.txt"
  bounded 45 system_profiler SPBluetoothDataType > "$f" 2>&1
  rc=$?
  if [ "$rc" = 124 ]; then result bluetooth FAIL "system_profiler SPBluetoothDataType timed out (45 s)"; return; fi
  # Newer releases say "State: On", older "Bluetooth Power: On".
  state=$(grep -m1 -E '^ *(State|Bluetooth Power):' "$f" | sed 's/.*: *//')
  addr=$(grep -m1 -E '^ *Address:' "$f" | sed 's/.*Address: *//')
  chip=$(grep -m1 -E '^ *Chipset:' "$f" | sed 's/.*: *//')
  fw=$(grep -m1 -E '^ *Firmware Version:' "$f" | sed 's/.*: *//')
  pgrep -x bluetoothd > /dev/null && bd=running || bd="not running"
  d="state ${state:-none}, chipset ${chip:-none}, address $( [ -n "$addr" ] && echo present || echo none), firmware ${fw:-none}, bluetoothd $bd"
  if [ "$state" = On ] && { [ -n "$addr" ] || [ -n "$chip" ]; }; then result bluetooth PASS "$d"
  else result bluetooth FAIL "$d"; fi
}

check_sound() {
  f="$OUT/sound.txt"
  bounded 45 system_profiler SPAudioDataType > "$TMPD/audio" 2>&1
  { echo "== system_profiler SPAudioDataType"; cat "$TMPD/audio"; } > "$f"
  outs=$(grep -c -E 'Output Channels:' "$TMPD/audio")
  defout=$(awk '/^        [^ ].*:$/ { n=$0; sub(/^ +/, "", n); sub(/:$/, "", n) } /Default Output Device: Yes/ { print n; exit }' "$TMPD/audio")
  pgrep -x coreaudiod > /dev/null && ca=running || ca="not running"
  snd=/System/Library/Sounds/Tink.aiff
  if ! have_user; then
    pr="skipped (no console user)"; prc=skip
  elif [ ! -f "$snd" ]; then
    pr="skipped ($snd missing)"; prc=skip
  else
    bounded 10 as_user afplay "$snd" >> "$f" 2>&1
    prc=$?
    [ "$prc" = 124 ] && pkill -U "$CUID" -x afplay 2> /dev/null
    pr="afplay rc $prc"
    [ "$prc" = 124 ] && pr="afplay timed out (10 s)"
  fi
  echo "== $pr; coreaudiod $ca" >> "$f"
  d="output devices $outs, default output ${defout:-none}, $pr, coreaudiod $ca (rc 0 does not prove the sound was audible)"
  if [ "$outs" -ge 1 ] && [ "$prc" = 0 ]; then result sound PASS "$d"
  elif [ "$outs" -ge 1 ] && [ "$prc" = skip ]; then result sound SKIP "$d"
  else result sound FAIL "$d"; fi
}

check_camera() {
  f="$OUT/camera.txt"
  bounded 45 system_profiler SPCameraDataType > "$TMPD/cam" 2>&1
  { echo "== system_profiler SPCameraDataType"; cat "$TMPD/cam"; } > "$f"
  cams=$(awk '/^    [^ ].*:$/ { n=$0; sub(/^ +/, "", n); sub(/:$/, "", n); printf "%s; ", n }' "$TMPD/cam" | sed 's/; $//')
  bounded 20 ioreg -p IOUSB -w0 > "$TMPD/usb" 2>&1
  { echo "== ioreg -p IOUSB"; cat "$TMPD/usb"; } >> "$f"
  usbcam=$(grep -m1 -i -E 'FaceTime|iSight|Camera' "$TMPD/usb" | sed 's/.*+-o //; s/@.*//')
  procs=""
  for p in VDCAssistant appleh13camerad AppleCameraAssistant UVCAssistant; do
    pgrep -x "$p" > /dev/null && procs="$procs $p"
  done
  echo "== camera daemons running:${procs:- none}" >> "$f"
  d="system_profiler: ${cams:-none}; USB: ${usbcam:-none}; camera daemons running:${procs:- none} (they start on demand); no camera app opened"
  if [ -n "$cams" ]; then result camera PASS "$d"; else result camera FAIL "$d"; fi
}

check_metal() {
  f="$OUT/metal.txt"
  bounded 45 system_profiler SPDisplaysDataType > "$f" 2>&1
  chip=$(grep -m1 'Chipset Model:' "$f" | sed 's/.*: *//')
  metal=$(grep -m1 -i -E '^ *Metal[^:]*:' "$f" | sed 's/^ *//')
  mtlc=/System/Library/Frameworks/Metal.framework/Versions/A/XPCServices/MTLCompilerService.xpc
  [ -d "$mtlc" ] && mc=present || mc=missing
  echo "== MTLCompilerService.xpc $mc" >> "$f"
  d="GPU ${chip:-none}, ${metal:-no Metal line}, MTLCompilerService $mc; a render test comes later from the metal lane"
  case "$metal" in
    ''|*[Nn]ot\ [Ss]upported*|*[Uu]nsupported*) mok=0 ;;
    *) mok=1 ;;
  esac
  case "$chip" in *4000*) cok=1 ;; *) cok=0 ;; esac
  if [ "$mok" = 1 ] && [ "$cok" = 1 ] && [ "$mc" = present ]; then result metal PASS "$d"; else result metal FAIL "$d"; fi
}

check_spotlight() {
  f="$OUT/spotlight.txt"
  st=$(bounded 20 mdutil -s / 2>&1)
  { echo "== mdutil -s /"; echo "$st"; } > "$f"
  # "-name Safari.app" can miss the app (seen on macOS 27 on the host); "-name Safari" and the bundle id find it.
  bounded 20 mdfind -name Safari > "$TMPD/mdfind" 2> /dev/null
  rc=$?
  { echo "== mdfind -name Safari (rc $rc)"; cat "$TMPD/mdfind"; } >> "$f"
  pat='^(/System/Volumes/[^ ]*)?/Applications/Safari\.app$|^/System/Cryptexes/App/System/Applications/Safari\.app$'
  hit=$(grep -m1 -E "$pat" "$TMPD/mdfind")
  if [ -z "$hit" ]; then
    bounded 20 mdfind 'kMDItemCFBundleIdentifier == "com.apple.Safari"' > "$TMPD/mdfind2" 2> /dev/null
    rc=$?
    { echo "== mdfind bundle id com.apple.Safari (rc $rc)"; cat "$TMPD/mdfind2"; } >> "$f"
    hit=$(grep -m1 -E "$pat" "$TMPD/mdfind2")
  fi
  case "$st" in *"Indexing enabled"*) ie=1 ;; *) ie=0 ;; esac
  d="$(echo "$st" | tail -n 1 | sed 's/^ *//; s/[. ]*$//'), mdfind $( [ "$rc" = 124 ] && echo 'timed out (20 s)' || echo "rc $rc"), Safari: ${hit:-not found}"
  if [ "$ie" = 1 ] && [ -n "$hit" ]; then result spotlight PASS "$d"; else result spotlight FAIL "$d"; fi
}

check_safari_net() {
  f="$OUT/safari-net.txt"
  code=$(bounded 20 curl -sS --max-time 15 -o /dev/null -w '%{http_code}' https://www.apple.com 2> "$TMPD/curl.err")
  rc=$?
  { echo "== curl https://www.apple.com rc $rc http $code"; cat "$TMPD/curl.err"; } > "$f"
  err=$(head -n 1 "$TMPD/curl.err")
  d="https://www.apple.com http ${code:-none} (curl rc $rc${err:+, $err}); the Safari GUI is covered by the app-launch loop"
  case "$code" in 200|301|302) result safari-net PASS "$d" ;; *) result safari-net FAIL "$d" ;; esac
}

check_terminal_shell() {
  f="$OUT/terminal-shell.txt"
  : > "$f"
  bad=""
  for spec in /usr/bin/login:4555 /usr/bin/su:4755 /usr/bin/sudo:4511; do
    p=${spec%%:*}; want=${spec##*:}
    got=$(stat -f '%Mp%Lp %u' "$p" 2> /dev/null)
    echo "$p: $got (want $want 0)" >> "$f"
    [ "$got" = "$want 0" ] || bad="$bad $(basename "$p")=${got:-missing}"
  done
  if have_user; then
    out=$(bounded 30 as_user /bin/zsh -l -c 'echo SHELLOK-$(id -un)' 2>&1)
    rc=$?
    { echo "== login shell rc $rc"; echo "$out"; } >> "$f"
    if printf '%s\n' "$out" | grep -qx "SHELLOK-$CUSER"; then sh_ok=1; sd="login shell ok"
    else sh_ok=0; sd="login shell failed (rc $rc: $(printf '%s' "$out" | tail -n 1 | cut -c1-120))"; fi
  else
    sh_ok=skip; sd="no console user for the login shell"
  fi
  d="$sd; set-ID $( [ -z "$bad" ] && echo 'login/su/sudo ok' || echo "wrong:$bad")"
  if [ "$sh_ok" = 1 ] && [ -z "$bad" ]; then result terminal-shell PASS "$d" "#74"
  elif [ "$sh_ok" = skip ] && [ -z "$bad" ]; then result terminal-shell SKIP "$d" "#74"
  else result terminal-shell FAIL "$d" "#74"; fi
}

check_aot() {
  f="$OUT/aot.txt"
  dir=/usr/lib/hao/aot
  if [ ! -d "$dir" ]; then
    jobs=$(bounded 15 launchctl list 2> /dev/null | grep -i hao | awk '{ print $3 }' | tr '\n' ' ')
    echo "no $dir; launchd jobs: ${jobs:-none}" > "$f"
    result aot FAIL "no $dir; hao launchd jobs: ${jobs:-none}"
    return
  fi
  bounded 30 find "$dir" -type f -name '*.aot' > "$TMPD/aot" 2> /dev/null
  n=$(grep -c . "$TMPD/aot")
  kb=0
  [ "$n" -gt 0 ] && kb=$(tr '\n' '\0' < "$TMPD/aot" | xargs -0 du -sk 2> /dev/null | awk '{ s += $1 } END { print s + 0 }')
  all=$(bounded 30 find "$dir" -type f 2> /dev/null | grep -c .)
  bounded 15 launchctl list > "$TMPD/lc" 2> /dev/null
  jobs=$(grep -i hao "$TMPD/lc" | awk '{ print $3 }' | tr '\n' ' ' | sed 's/ $//')
  if have_user; then
    ujobs=$(bounded 15 launchctl print "gui/$CUID" 2> /dev/null | grep -i -o -E '[A-Za-z0-9._-]*hao[A-Za-z0-9._-]*' | sort -u | tr '\n' ' ' | sed 's/ $//')
  fi
  procs=$(ps -axo comm= 2> /dev/null | grep -i hao | sed 's|.*/||' | sort -u | tr '\n' ' ' | sed 's/ $//')
  { echo "== $dir: $n .aot files, $all files, ${kb} KB in .aot"; cat "$TMPD/aot"
    echo "== launchctl list (system) hao jobs: ${jobs:-none}"; echo "== gui/$CUID hao names: ${ujobs:-none}"
    echo "== hao processes: ${procs:-none}"; } > "$f"
  d="$dir: $n .aot files ($((kb / 1024)) MB), $all files in all; launchd jobs: ${jobs:-none}; user domain: ${ujobs:-none}; processes: ${procs:-none}"
  if [ "$n" -ge 1 ]; then result aot PASS "$d"; else result aot FAIL "$d"; fi
}

check_hao_verify() {
  f="$OUT/hao-verify.txt"
  hao=""
  for c in /usr/local/bin/hao /usr/bin/hao; do [ -x "$c" ] && { hao=$c; break; }; done
  if [ -z "$hao" ]; then
    result hao-verify SKIP "no hao command on this image (bench runs it from the installer copy)"
    return
  fi
  bounded 300 "$hao" verify / > "$f" 2>&1 < /dev/null
  rc=$?
  last=$(grep -v '^ *$' "$f" | tail -n 1 | cut -c1-200)
  if [ "$rc" = 0 ]; then result hao-verify PASS "$hao verify / rc 0: $last"
  elif [ "$rc" = 124 ]; then result hao-verify FAIL "$hao verify / timed out (300 s): $last"
  else result hao-verify FAIL "$hao verify / rc $rc: $last"; fi
}

check_filevault() {
  f="$OUT/filevault.txt"
  fv=$(bounded 20 fdesetup status 2>&1)
  bounded 30 diskutil apfs list > "$TMPD/apfs" 2>&1
  { echo "== fdesetup status"; echo "$fv"; echo "== diskutil apfs list"; cat "$TMPD/apfs"; } > "$f"
  # Lines about encryption or FileVault for the Data volume (or any volume, if the Role line is not found).
  datafv=$(awk '/\(Data\)/ { d=1; next } /APFS Volume Disk \(Role\)/ { d=0 } d && /FileVault|Encrypt|Progress|Paused|Conversion/ { sub(/^[ |+-]*/, ""); printf "%s; ", $0 }' "$TMPD/apfs" | sed 's/; $//')
  [ -n "$datafv" ] || datafv=$(grep -i -E 'Encrypt.*(Progress|%)|Paused' "$TMPD/apfs" | sed 's/^[ |+-]*//' | tr '\n' ';')
  all="$fv $datafv"
  pct=$(printf '%s' "$all" | grep -o -E '[0-9]+(\.[0-9]+)?[ ]*%|Percent completed = [0-9.]+' | head -n 1 | grep -o -E '[0-9]+(\.[0-9]+)?')
  fvl=$(echo "$fv" | head -n 1)
  d="$fvl; Data: ${datafv:-no FileVault lines}${pct:+; progress $pct%}"
  if printf '%s' "$all" | grep -q -i 'paused'; then result filevault FAIL "paused${pct:+ at $pct%}; $d"; return; fi
  if printf '%s' "$all" | grep -q -i -E 'in progress|encrypting|decrypting'; then
    sleep 30
    fv2=$(bounded 20 fdesetup status 2>&1)
    pct2=$(printf '%s' "$fv2" | grep -o -E 'Percent completed = [0-9.]+|[0-9]+(\.[0-9]+)?[ ]*%' | head -n 1 | grep -o -E '[0-9]+(\.[0-9]+)?')
    { echo "== fdesetup status after 30 s"; echo "$fv2"; } >> "$f"
    if [ -n "$pct" ] && [ "$pct" = "$pct2" ]; then
      result filevault FAIL "conversion in progress but no progress in 30 s ($pct%); $d"
    else
      result filevault PASS "conversion in progress, ${pct:-?}% -> ${pct2:-?}% in 30 s (not paused); $d"
    fi
    return
  fi
  case "$fvl" in
    *"FileVault is Off"*) result filevault PASS "$d" ;;
    *"FileVault is On"*) result filevault PASS "$d" ;;
    *) result filevault FAIL "unrecognised fdesetup output; $d" ;;
  esac
}

check_timing() {
  f="$OUT/timing.txt"
  now=$(date +%s)
  lwp=""; dkp=""; wsp=""
  have_user && lwp=$(pgrep -U "$CUID" -x loginwindow | head -n 1)
  [ -n "$lwp" ] || lwp=$(pgrep -x loginwindow | head -n 1)
  have_user && dkp=$(pgrep -U "$CUID" -x Dock | head -n 1)
  wsp=$(pgrep -x WindowServer | head -n 1)
  lw=""; dk=""; ws=""
  [ -n "$lwp" ] && lw=$(lstart_epoch "$lwp")
  [ -n "$dkp" ] && dk=$(lstart_epoch "$dkp")
  [ -n "$wsp" ] && ws=$(lstart_epoch "$wsp")
  # A start time before kern.boottime (ps can report 1976 for early processes) is shown as "unknown".
  rel() {
    if [ -z "$1" ] || [ -z "$BOOT" ]; then echo none
    elif [ "$1" -lt "$BOOT" ]; then echo "unknown (ps start before boot)"
    else echo "+$(($1 - BOOT)) s"; fi
  }
  {
    echo "kern.boottime: $(sysctl -n kern.boottime 2> /dev/null)"
    echo "now: $now ($(date))"
    echo "uptime: $(uptime)"
    echo "WindowServer pid ${wsp:-none} start ${ws:-none} $(rel "$ws")"
    echo "loginwindow pid ${lwp:-none} start ${lw:-none} $(rel "$lw")"
    echo "Dock pid ${dkp:-none} start ${dk:-none} $(rel "$dk")"
  } > "$f"
  up="none"; [ -n "$BOOT" ] && up="$((now - BOOT)) s"
  result timing PASS "uptime $up; boot->WindowServer $(rel "$ws"), boot->loginwindow $(rel "$lw"), boot->Dock $(rel "$dk") (process start times, 1 s resolution; a process restarted since boot shows its restart)"
}

check_controlcenter_dock() {
  f="$OUT/controlcenter-dock.txt"
  if ! have_user; then result controlcenter-dock SKIP "no console user (CUSER/CUID)"; return; fi
  cc=$(pgrep -U "$CUID" -x ControlCenter | head -n 1)
  dk=$(pgrep -U "$CUID" -x Dock | head -n 1)
  ws=$(pgrep -x WindowServer | head -n 1)
  ps -o pid,user,lstart,comm -p "${cc:-0},${dk:-0},${ws:-0}" > "$f" 2>&1
  st() { if [ -n "$1" ]; then echo "pid $1"; else echo "not running"; fi; }
  d="ControlCenter $(st "$cc"), Dock $(st "$dk"), WindowServer $(st "$ws") (user $CUSER)"
  if [ -n "$cc" ] && [ -n "$dk" ]; then result controlcenter-dock PASS "$d"; else result controlcenter-dock FAIL "$d"; fi
}

check_crashes() {
  f="$OUT/crashes-since-boot.txt"
  if [ -z "$BOOT" ]; then result crashes-since-boot SKIP "kern.boottime unreadable"; return; fi
  ref="$TMPD/boot-ref"
  touch -t "$(date -r "$BOOT" +%Y%m%d%H%M.%S)" "$ref"
  : > "$TMPD/crash"
  dirs="/Library/Logs/DiagnosticReports"
  [ -n "$CUSER" ] && dirs="$dirs /Users/$CUSER/Library/Logs/DiagnosticReports"
  for dd in $dirs; do
    [ -d "$dd" ] || continue
    bounded 30 find "$dd" -type f \( -name '*.ips' -o -name '*.panic' \) -newer "$ref" >> "$TMPD/crash" 2> /dev/null
  done
  ips=$(grep -c '\.ips$' "$TMPD/crash")
  panics=$(grep -c -E '\.panic$|/[^/]*[Pp]anic[^/]*\.ips$' "$TMPD/crash")
  { echo "reports newer than kern.boottime $BOOT ($(date -r "$BOOT")) in $dirs:"; cat "$TMPD/crash"; } > "$f"
  names=$(sed 's|.*/||; s/-[0-9]\{4\}-[0-9]\{2\}-[0-9]\{2\}-[0-9]\{6\}.*//' "$TMPD/crash" | sort | uniq -c | sort -rn | head -n 8 | awk '{ printf "%s x%s, ", $2, $1 }' | sed 's/, $//')
  d="panics $panics, app .ips $ips${names:+ ($names)}; a panic report written this boot is from the previous boot"
  if [ "$panics" = 0 ]; then result crashes-since-boot PASS "$d"; else result crashes-since-boot FAIL "$d"; fi
}

# --- run -------------------------------------------------------------------------------------------------------

run_check wifi check_wifi
run_check bluetooth check_bluetooth
run_check sound check_sound
run_check camera check_camera
run_check metal check_metal
run_check spotlight check_spotlight
run_check safari-net check_safari_net
run_check terminal-shell check_terminal_shell
run_check aot check_aot
run_check hao-verify check_hao_verify
run_check filevault check_filevault
run_check timing check_timing
run_check controlcenter-dock check_controlcenter_dock
run_check crashes-since-boot check_crashes

rm -rf "$TMPD"
echo "HAOCHECK END behaviours"
