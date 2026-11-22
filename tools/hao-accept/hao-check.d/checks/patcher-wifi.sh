#!/bin/sh
# hao-check plug-in, patcher: Wi-Fi works through OCLP's Broadcom stack (PA2).
# Run as root over SSH on the laptop's dev image, with OUT, CUSER, CUID set. Changes nothing: it uses the network the
# bench setup joined ("Internet") and sends one HTTPS request bound to the Wi-Fi interface (no route change).
# Checks: wifi-driver (an AirPort driver is loaded), wifi-traffic (an address on the Wi-Fi interface and an HTTPS answer
# through it), wifi-daemons (no crash of airportd, wifid, WiFiAgent, wifip2pd or wifianalyticsd since boot).
PATH=/usr/bin:/bin:/usr/sbin:/sbin:/usr/libexec
export PATH
LC_ALL=C
export LC_ALL
NAME=patcher-wifi
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


dev=$(networksetup -listallhardwareports 2> /dev/null | awk '/^Hardware Port: Wi-Fi/ {getline; print $2; exit}')
if [ -z "$dev" ]; then
  result wifi-driver SKIP "no Wi-Fi hardware port on this image"
  echo "HAOCHECK END $NAME"; exit 0
fi
loaded_kexts "$E/loaded.txt"
drv=$(grep -i 'airport' "$E/loaded.txt" | awk '{print $1 "(" $2 ")"}' | tr '\n' ' ')
compat=
for f in /System/Library/PrivateFrameworks/IO80211.framework/Versions/A/IO80211OCLP.dylib \
         /System/Library/PrivateFrameworks/WiFiPeerToPeer.framework/Versions/A/WiFiPeerToPeerOCLP.dylib; do
  [ -f "$f" ] && compat="$compat $(basename "$f")"
done
if [ -n "$drv" ]; then result wifi-driver PASS "$dev: $drv; OCLP wrappers behind the compat libraries:${compat:- none}"
else result wifi-driver FAIL "$dev present but no AirPort driver loaded; OCLP wrappers:${compat:- none}"; fi

bounded 20 networksetup -getairportnetwork "$dev" > "$E/network.txt" 2>&1
ip=$(ipconfig getifaddr "$dev" 2> /dev/null)
if [ -z "$ip" ]; then
  result wifi-traffic FAIL "no address on $dev ($(cat "$E/network.txt")); bench's setup joins \"Internet\""
else
  code=$(bounded 30 curl -sS --interface "$dev" -o /dev/null -w '%{http_code}' --max-time 25 https://www.apple.com 2> "$E/curl.txt")
  case "$code" in
    200|301|302) result wifi-traffic PASS "$dev $ip ($(cat "$E/network.txt")): HTTPS $code through $dev" ;;
    *) result wifi-traffic FAIL "$dev $ip: HTTPS through $dev gave '${code:-nothing}' ($(head -c 200 "$E/curl.txt"))" ;;
  esac
fi
bounded 60 log show --last 10m --style compact --predicate 'process == "airportd"' > "$E/airportd.log" 2> /dev/null
c=$(crashes_since_boot 'airportd|wifid|WiFiAgent|wifip2pd|wifianalyticsd' | tr '\n' ' ')
if [ -z "$c" ]; then result wifi-daemons PASS "no Wi-Fi daemon crash since boot"
else result wifi-daemons FAIL "crash reports since boot: $c"; fi

echo "HAOCHECK END patcher-wifi"
