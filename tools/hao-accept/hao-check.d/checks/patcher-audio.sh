#!/bin/sh
# hao-check plug-in, patcher: sound through 27's audio stack on Tahoe's AppleHDA (PA4).
# Run as root over SSH on the laptop's dev image, with OUT, CUSER, CUID set. Changes nothing; plays one short system sound.
# Checks: audio-output (a built-in output device is the default), audio-play (a sound plays to the end and coreaudiod
# and audiomxd keep their pids across it), audio-crashes (no coreaudiod, audiomxd or AudioComponentRegistrar crash since
# boot).
PATH=/usr/bin:/bin:/usr/sbin:/sbin:/usr/libexec
export PATH
LC_ALL=C
export LC_ALL
NAME=patcher-audio
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


bounded 45 system_profiler SPAudioDataType > "$E/audio.txt" 2>&1
def=$(awk '/^ {8}[^ ].*:$/ {dev = $0} /Default Output Device: Yes/ {print dev; exit}' "$E/audio.txt" | sed 's/^ *//; s/:$//')
case "$def" in
  *Speaker*|*Built-in*|*Internal*) result audio-output PASS "default output: $def" ;;
  "") result audio-output FAIL "no default output device in system_profiler" ;;
  *) result audio-output FAIL "default output is '$def', not the built-in one" ;;
esac
a1=$(pgrep -x coreaudiod | head -1); m1=$(pgrep -x audiomxd | head -1)
if [ -n "$CUSER" ] && [ "$CUSER" != root ]; then bounded 10 as_user afplay /System/Library/Sounds/Ping.aiff; rc=$?
else bounded 10 afplay /System/Library/Sounds/Ping.aiff; rc=$?; fi
sleep 2
a2=$(pgrep -x coreaudiod | head -1); m2=$(pgrep -x audiomxd | head -1)
d="afplay rc $rc; coreaudiod ${a1:-none}->${a2:-none}; audiomxd ${m1:-none}->${m2:-none}"
if [ "$rc" = 0 ] && [ -n "$a1" ] && [ "$a1" = "$a2" ] && [ "$m1" = "$m2" ]; then result audio-play PASS "$d"
else result audio-play FAIL "$d"; fi
bounded 60 log show --last 2m --style compact --predicate 'process IN {"coreaudiod","audiomxd"}' > "$E/audio.log" 2> /dev/null
c=$(crashes_since_boot 'coreaudiod|audiomxd|AudioComponentRegistrar|com.apple.audio.DriverHelper' | tr '\n' ' ')
if [ -z "$c" ]; then result audio-crashes PASS "no audio daemon crash since boot"
else result audio-crashes FAIL "crash reports since boot: $c"; fi

echo "HAOCHECK END patcher-audio"
