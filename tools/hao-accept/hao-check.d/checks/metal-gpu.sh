#!/bin/sh
# metal-gpu.sh: metal's GPU checks for hao-check (IMG1): Metal render (argument-buffer probes, SceneKit offscreen), the HD 4000
# shader-cache seeds under hao verify, Quick Look 3D (.scn, .usdz), WebGL in Safari. Needs metal's kit on the laptop (see need_kit).
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

render() {
  need_kit metal-render || return
  f="$OUT/metal-probes.txt"; : > "$f"; bad=""
  for n in probe10 probe11 probe12 probe14 probe16 probe17 probe18; do
    echo "== $n" >> "$f"; bounded 60 as_user "$PROBES/$n" >> "$f" 2>&1; rc=$?; echo "exit $rc" >> "$f"
    [ "$rc" = 0 ] || bad="$bad $n(rc $rc)"
  done
  grep -qE 'WRONG|FAIL' "$f" && bad="$bad $(grep -cE 'WRONG|FAIL' "$f") WRONG/FAIL lines"
  if [ -z "$bad" ]; then result metal-argbuf-probes PASS "7 probes exit 0, no WRONG/FAIL" "#23"
  else result metal-argbuf-probes FAIL "$bad (see metal-probes.txt)" "#23"; fi
  png="$OUT/metal-scene.png"; rm -f "$png"
  bounded 90 as_user "$KIT/scn-snapshot" "$KIT/box.scn" "$png" > "$OUT/metal-scene.txt" 2>&1
  if [ -f "$png" ] && d=$(image_ok "$png"); then result metal-scenekit-render PASS "$d"
  else result metal-scenekit-render FAIL "${d:-no image}: $(tail -c 200 "$OUT/metal-scene.txt")"; fi
}

caches() {
  h=$(command -v hao || ls /usr/local/bin/hao 2>/dev/null)
  if [ -z "$h" ]; then result metal-shader-seeds SKIP "no hao command on this image"; return; fi
  bounded 600 "$h" verify / > "$OUT/metal-verify.txt" 2>&1; rc=$?
  n=$(grep -c 'content *intel-hd4000 .*com.apple.metal' "$OUT/metal-verify.txt")
  other=$(grep -E '^ *(content|missing|mode|owner|flags) ' "$OUT/metal-verify.txt" | grep -vc 'com.apple.metal')
  c=$(crashes MTLCompilerService | wc -l | tr -d ' ')
  if [ "$rc" = 0 ] && [ "$c" = 0 ]; then result metal-shader-seeds PASS "hao verify rc 0 (seed changes: $n); no MTLCompilerService crash"
  elif [ "$rc" = 4 ] && [ "$other" = 0 ] && [ "$c" = 0 ]; then
    result metal-shader-seeds PASS "KNOWN until the seed rule lands (builder 1317): rc 4 with only $n HD 4000 Metal caches changed"
  else result metal-shader-seeds FAIL "hao verify rc $rc, $other non-cache differences, $c MTLCompilerService crash reports"; fi
}

quicklook() {
  need_kit metal-quicklook || return
  d="$OUT/metal-ql"; rm -rf "$d"; mkdir -p "$d"; chown "$CUSER" "$d"
  for x in scn usdz; do
    issue="#22"; [ "$x" = usdz ] && issue="#26"
    before=$(crashes 'QuickLook|qlmanage' | wc -l)
    bounded 120 as_user qlmanage -t -s 512 -o "$d" "$KIT/box.$x" > "$d/$x.log" 2>&1
    png=$(ls "$d"/box.$x*.png 2>/dev/null | head -1)
    after=$(crashes 'QuickLook|qlmanage' | wc -l)
    if [ -n "$png" ] && r=$(image_ok "$png") && [ "$after" = "$before" ]; then result metal-quicklook-$x PASS "$r" "$issue"
    else result metal-quicklook-$x FAIL "${r:-no thumbnail}; crash reports $before -> $after" "$issue"; fi
  done
}

webgl() {
  # The page turns pure green on a WebGL PASS, pure red otherwise (no Safari automation needed); the screenshot is graded.
  need_kit metal-webgl || return
  cp "$KIT/webgl.html" /private/var/tmp/metal-webgl.html; chmod 644 /private/var/tmp/metal-webgl.html
  bounded 20 as_user open -a Safari /private/var/tmp/metal-webgl.html; sleep 15
  bounded 10 as_user screencapture -x "$OUT/metal-webgl.png"
  bounded 10 as_user pkill -x Safari
  s=$("$KIT/imgstat" "$OUT/metal-webgl.png" 2>&1); set -- $s
  if awk -v g="${10}" 'BEGIN{exit !(g>=20)}'; then result metal-webgl PASS "page green: $s" "#23"
  elif awk -v r="${12}" 'BEGIN{exit !(r>=20)}'; then result metal-webgl FAIL "page red (WebGL draw or read-back wrong): $s" "#23"
  else result metal-webgl FAIL "no page signal (page not shown?): $s; see metal-webgl.png" "#23"; fi
}

render; caches; quicklook; webgl
