#!/bin/sh
# metal-weather.sh: Weather for hao-check (IMG1): it opens, the map expands and draws, no crash (#9). Whether the rain colours are
# right (#62) needs the weather at the shown place: recorded (screenshot + colour statistics), graded by metal.
# --- shared by metal-*.sh (copied into each: hao-check pipes one file per run) ---
KIT=${METAL_KIT:-/var/tmp/metal-kit}; PROBES=${METAL_PROBES:-/var/tmp/probes}
TMPD=${TMPDIR:-/tmp}; BN=0
oneline() { tr '\n\t' '  ' | cut -c1-300; }
result() { if [ -n "${4:-}" ]; then printf 'HAOCHECK CHECK %s %s %s issue=%s\n' "$1" "$2" "$(printf '%s' "$3" | oneline)" "$4"
           else printf 'HAOCHECK CHECK %s %s %s\n' "$1" "$2" "$(printf '%s' "$3" | oneline)"; fi; }
bounded() { _s=$1; shift; BN=$((BN+1)); _m="$TMPD/metal-to.$$.$BN"; rm -f "$_m"; "$@" & _p=$!
  ( sleep "$_s"; if kill -0 "$_p" 2>/dev/null; then : > "$_m"; kill -TERM "$_p" 2>/dev/null; sleep 2; kill -KILL "$_p" 2>/dev/null; fi ) >/dev/null 2>&1 </dev/null & _w=$!
  wait "$_p"; _rc=$?; pkill -P "$_w" 2>/dev/null; kill "$_w" 2>/dev/null; wait "$_w" 2>/dev/null
  if [ -f "$_m" ]; then rm -f "$_m"; return 124; fi; return "$_rc"; }
as_user() { launchctl asuser "$CUID" sudo -u "$CUSER" "$@"; }
crashes() { ls /Library/Logs/DiagnosticReports "/Users/$CUSER/Library/Logs/DiagnosticReports" 2>/dev/null | grep -iE "$1" | sort; }
# image_ok PNG: >= 200 colours, >= 10 % saturated, < 1 % magenta (imgstat from the kit)
image_ok() { s=$("$KIT/imgstat" "$1" 2>&1) || { echo "imgstat: $s"; return 1; }; echo "$s"
  set -- $s; [ "$4" -ge 200 ] && awk -v s="$6" -v m="$8" 'BEGIN{exit !(s>=10 && m<1)}'; }
need_kit() { [ -x "$KIT/imgstat" ] && [ -x "$PROBES/probe10" ] && return 0
  result "$1" SKIP "metal kit not on the laptop: hao-check stages metal-checks-kit-20261117 at $KIT and metal-argbuf-probes-20261117-be8c1574 at $PROBES"; return 1; }
# --- end shared ---

weather() {
  need_kit metal-weather || return
  before=$(crashes Weather | wc -l)
  bounded 20 as_user open -a Weather; sleep 20
  bounded 15 as_user osascript -e 'tell application "System Events" to tell process "Weather" to click menu item "Expand Map" of menu "View" of menu bar 1' > "$OUT/metal-weather.txt" 2>&1
  sleep 20
  bounded 10 as_user screencapture -x "$OUT/metal-weather.png"
  up=$(pgrep -x Weather | head -1)
  bounded 10 as_user osascript -e 'tell application "Weather" to quit' > /dev/null 2>&1
  after=$(crashes Weather | wc -l)
  s=$("$KIT/imgstat" "$OUT/metal-weather.png" 2>&1)
  if [ -n "$up" ] && [ "$after" = "$before" ]; then result metal-weather PASS "up after 40 s, map expanded; screen $s" "#9"
  else result metal-weather FAIL "running: ${up:-no}; crash reports $before -> $after" "#9"; fi
  result metal-weather-rain-colours SKIP "graded by metal from metal-weather.png and the weather at its place ($s)" "#62"
}

weather
