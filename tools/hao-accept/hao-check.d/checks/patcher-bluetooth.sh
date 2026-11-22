#!/bin/sh
# hao-check plug-in, patcher: Bluetooth through BlueToolFixup (PA3).
# Run as root over SSH on the laptop's dev image, with OUT, CUSER, CUID set. Changes nothing: no pairing, no power change.
# Checks: bluetooth-daemon (bluetoothd keeps one pid for 60 s and the controller reports a state), bluetooth-settings
# (the Bluetooth pane of System Settings opens and stays up 20 s with no crash: the regression check of #45), and
# bluetooth-crashes (no bluetoothd or BlueTool crash since boot). Pairing needs a device: not hands-free, not here.
PATH=/usr/bin:/bin:/usr/sbin:/sbin:/usr/libexec
export PATH
LC_ALL=C
export LC_ALL
NAME=patcher-bluetooth
if [ -z "${OUT:-}" ]; then echo "HAOCHECK CHECK env FAIL OUT is not set"; echo "HAOCHECK END $NAME"; exit 2; fi
if [ -z "${CUSER:-}" ]; then CUSER=$(stat -f %Su /dev/console 2> /dev/null); fi
if [ -z "${CUID:-}" ] && [ -n "$CUSER" ]; then CUID=$(id -u "$CUSER" 2> /dev/null); fi
E="$OUT/$NAME"
mkdir -p "$E"

oneline() { tr '\n\t' '  ' | tr -s ' ' | sed 's/^ //; s/ $//' | cut -c1-400; }
result() {   # result CHECK STATUS DETAIL [ISSUE]
  _d=$(printf '%s' "$3" | oneline)
  if [ -n "${4:-}" ]; then printf 'HAOCHECK CHECK %s %s %s issue=%s\n' "$1" "$2" "$_d" "$4"
  else printf 'HAOCHECK CHECK %s %s %s\n' "$1" "$2" "$_d"; fi
}
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
# crashes_since_boot REGEX: crash and panic report files written since boot whose process name matches REGEX.
crashes_since_boot() {
  _b=$(sysctl -n kern.boottime 2> /dev/null | sed -E 's/.*sec = ([0-9]+).*/\1/')
  [ -n "$_b" ] || return 0
  touch -t "$(date -r "$_b" +%Y%m%d%H%M.%S)" "$E/.boot.ref"
  for _d in /Library/Logs/DiagnosticReports "/Users/$CUSER/Library/Logs/DiagnosticReports"; do
    [ -d "$_d" ] && find "$_d" -maxdepth 2 -type f \( -name '*.ips' -o -name '*.crash' -o -name '*.panic' \) -newer "$E/.boot.ref" 2> /dev/null
  done | while read -r _f; do
    _n=$(basename "$_f")
    printf '%s\n' "$_n" | grep -E -q "^($1)[-_.]" && printf '%s\n' "$_n"
  done
}
# loaded_kexts FILE: "bundle-id (version)" for every loaded kext.
loaded_kexts() {
  { bounded 60 kmutil showloaded --list-only 2> /dev/null || bounded 60 kextstat -l 2> /dev/null; } |
    awk '{for (i = 1; i < NF; i++) if ($(i+1) ~ /^\([0-9]/) {v = $(i+1); gsub(/[()]/, "", v); print $i, v}}' | sort -u > "$1"
}


p1=$(pgrep -x bluetoothd | head -1)
bounded 45 system_profiler SPBluetoothDataType > "$E/bluetooth.txt" 2>&1
state=$(awk -F': *' '/State:/ {print $2; exit}' "$E/bluetooth.txt")
chip=$(awk -F': *' '/Chipset:/ {print $2; exit}' "$E/bluetooth.txt")
sleep 60
p2=$(pgrep -x bluetoothd | head -1)
d="bluetoothd pid ${p1:-none} -> ${p2:-none} over 60 s; state ${state:-?}; chipset ${chip:-?}"
if [ -n "$p1" ] && [ "$p1" = "$p2" ] && [ -n "$state" ]; then result bluetooth-daemon PASS "$d"
else result bluetooth-daemon FAIL "$d"; fi

if [ -z "$CUSER" ] || [ "$CUSER" = root ]; then
  result bluetooth-settings SKIP "no console user" "#45"
else
  before=$(crashes_since_boot 'System Settings|SystemSettings|com.apple.BluetoothSettings|BluetoothSettings|bluetoothd|BluetoothUIServer' | sort)
  as_user open "x-apple.systempreferences:com.apple.BluetoothSettings" > /dev/null 2>&1
  sleep 20
  up=$(pgrep -x "System Settings" | head -1)
  after=$(crashes_since_boot 'System Settings|SystemSettings|com.apple.BluetoothSettings|BluetoothSettings|bluetoothd|BluetoothUIServer' | sort)
  new=$(printf '%s\n%s\n' "$before" "$after" | sort | uniq -u | tr '\n' ' ')
  as_user osascript -e 'tell application "System Settings" to quit' > /dev/null 2>&1
  if [ -n "$up" ] && [ -z "$new" ]; then result bluetooth-settings PASS "Bluetooth pane up 20 s, no crash" "#45"
  else result bluetooth-settings FAIL "System Settings pid ${up:-gone}; new crash reports: ${new:-none}" "#45"; fi
fi
c=$(crashes_since_boot 'bluetoothd|BlueTool|bluetoothuserd|BTServer' | tr '\n' ' ')
if [ -z "$c" ]; then result bluetooth-crashes PASS "no Bluetooth daemon crash since boot"
else result bluetooth-crashes FAIL "crash reports since boot: $c"; fi

echo "HAOCHECK END patcher-bluetooth"
