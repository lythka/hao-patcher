#!/bin/sh
# metal-livetext.sh: Live Text for hao-check (IMG1): Vision text recognition with the HD 4000 runtime, and mediaanalysisd.
# Live Text has not yet been seen working on the HD 4000 (#24): a FAIL is expected until metal says otherwise.
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

ocr() {
  need_kit metal-livetext || return
  bounded 300 as_user env HAO_LIVE_TEXT_DIAGNOSTICS=1 "$KIT/ocr_probe" > "$OUT/metal-ocr.txt" 2> "$OUT/metal-ocr.err"; rc=$?
  got=$(tr '\n' '|' < "$OUT/metal-ocr.txt")
  if [ "$rc" = 0 ] && [ "$got" = "Live Text on Intel|HD Graphics 4000|" ] && grep -q 'event=detector-bind-pass' "$OUT/metal-ocr.err"; then
    result metal-livetext-ocr PASS "both lines recognized, detector bound" "#24"
  else result metal-livetext-ocr FAIL "rc $rc, got '$got'; $(grep -m1 -iE 'error|refus' "$OUT/metal-ocr.err")" "#24"; fi
}

daemon() {
  p=$(pgrep -x mediaanalysisd | head -1); c=$(crashes mediaanalysisd | wc -l | tr -d ' ')
  rv=$(nvram 4D1FDA02-38C7-4A6A-9CC6-4BCCA8B30102:revblock 2>/dev/null | cut -f2)
  ps -axo pid,pcpu,rss,etime,comm | grep -E 'mediaanalysisd|photoanalysisd' | grep -v grep > "$OUT/metal-mediaanalysisd.txt"
  if [ "$c" = 0 ]; then result metal-mediaanalysisd PASS "revblock ${rv:-unset}, pid ${p:-not running}, no crash report"
  else result metal-mediaanalysisd FAIL "$c mediaanalysisd crash reports (revblock ${rv:-unset})"; fi
}

ocr; daemon
