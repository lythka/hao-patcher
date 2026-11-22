#!/bin/sh
# hao-check plug-in, patcher: the FaceTime HD camera (USB) is there and its services stay up (PA5).
# Run as root over SSH on the laptop's dev image, with OUT, CUSER, CUID set. Changes nothing. The live preview is the
# app sweep's Photo Booth launch (window, no crash); a frame check needs the screen and is bench's.
# Checks: camera-device (the camera on USB and in system_profiler), camera-crashes (no VDCAssistant, camera daemon or
# Photo Booth crash since boot).
PATH=/usr/bin:/bin:/usr/sbin:/sbin:/usr/libexec
export PATH
LC_ALL=C
export LC_ALL
NAME=patcher-camera
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


bounded 45 system_profiler SPCameraDataType > "$E/camera.txt" 2>&1
name=$(awk -F': *' '/^ {4}[^ ].*:$/ {gsub(/^ +|:$/, ""); print; exit}' "$E/camera.txt")
usb=$(ioreg -p IOUSB -w0 2> /dev/null | grep -o 'FaceTime[^<@]*' | head -1)
if [ -n "$name" ]; then result camera-device PASS "system_profiler: $name; USB: ${usb:-not named}"
else result camera-device FAIL "no camera in system_profiler; USB: ${usb:-none}"; fi
c=$(crashes_since_boot 'VDCAssistant|appleh13camerad|UVCAssistant|cameracaptured|Photo Booth' | tr '\n' ' ')
if [ -z "$c" ]; then result camera-crashes PASS "no camera service crash since boot"
else result camera-crashes FAIL "crash reports since boot: $c"; fi

echo "HAOCHECK END patcher-camera"
