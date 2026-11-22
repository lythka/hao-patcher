#!/bin/sh
# hao-check plug-in, patcher: AirDrop's hands-free part (PA6).
# Run as root over SSH on the laptop's dev image, with OUT, CUSER, CUID set. Changes nothing persistent: it opens Finder's
# AirDrop window as the console user and closes it after; it does not change who can discover the Mac.
# Checks: airdrop-awdl (with the AirDrop window open, awdl0 comes up within 30 s: the regression check of #51),
# airdrop-sharingd (sharingd up, no sharingd crash since boot), airdrop-transfer (an iPhone transfer needs the owner's
# phone: always SKIP here, known open #63).
PATH=/usr/bin:/bin:/usr/sbin:/sbin:/usr/libexec
export PATH
LC_ALL=C
export LC_ALL
NAME=patcher-airdrop
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


if [ -z "$CUSER" ] || [ "$CUSER" = root ]; then
  result airdrop-awdl SKIP "no console user" "#51"
else
  mode=$(as_user defaults read com.apple.sharingd DiscoverableMode 2> /dev/null)
  as_user open /System/Library/CoreServices/Finder.app/Contents/Applications/AirDrop.app > /dev/null 2>&1
  up=; n=0
  while [ $n -lt 30 ]; do
    ifconfig awdl0 2> /dev/null | grep -q 'status: active' && { up=$n; break; }
    sleep 1; n=$((n + 1))
  done
  ifconfig awdl0 > "$E/awdl0.txt" 2>&1
  as_user osascript -e 'tell application "Finder" to close (every window whose name is "AirDrop")' > /dev/null 2>&1
  if [ -n "$up" ]; then result airdrop-awdl PASS "awdl0 active after ${up} s with the AirDrop window open; DiscoverableMode ${mode:-default}" "#51"
  else result airdrop-awdl FAIL "awdl0 not active within 30 s ($(grep -E 'flags|status' "$E/awdl0.txt" | head -2)); DiscoverableMode ${mode:-default}" "#51"; fi
fi
s=$(pgrep -x sharingd | head -1)
c=$(crashes_since_boot 'sharingd|AirDrop' | tr '\n' ' ')
if [ -n "$s" ] && [ -z "$c" ]; then result airdrop-sharingd PASS "sharingd pid $s, no crash since boot"
else result airdrop-sharingd FAIL "sharingd pid ${s:-none}; crash reports since boot: ${c:-none}"; fi
result airdrop-transfer SKIP "an iPhone-to-Mac transfer needs the owner's phone; known open" "#63"

echo "HAOCHECK END patcher-airdrop"
