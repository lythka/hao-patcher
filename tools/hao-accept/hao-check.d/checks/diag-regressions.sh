#!/bin/sh
# hao-check plug-in (diag): one regression check per fixed GitHub issue of lythka/hao-patcher (closed as completed or
# "fixed: pending validation"). TEST-ONLY: runs on a dev image of the MacBookPro9,2 test laptop, never in a release.
# The full list, including the checks a script cannot do, is the diagnosis lane's regression table (same R<issue> ids).
#
# Run as root over SSH on the laptop (POSIX sh), with OUT (evidence folder), CUSER and CUID (console user) set.
# Optional: KIT, a folder with the test files (see kit_file below); rows that need a missing file print SKIP.
# Output: "HAOCHECK CHECK <name> <PASS|FAIL|SKIP> <detail> issue=#N" per check, then "HAOCHECK END diag-regressions".
# Changes nothing persistent: it opens apps as the console user (as the app sweep does), quits them with SIGTERM, and
# writes only under $OUT. #74 (Terminal) and #79 (sleep/wake) are run by behaviours.sh and the orchestrator, not here.

PATH=/usr/bin:/bin:/usr/sbin:/sbin:/usr/libexec:/usr/local/bin
export PATH
LC_ALL=C
export LC_ALL

if [ -z "${OUT:-}" ]; then
  echo "HAOCHECK CHECK env FAIL OUT is not set"
  echo "HAOCHECK END diag-regressions"
  exit 2
fi
mkdir -p "$OUT/diag" 2> /dev/null
O="$OUT/diag"
if [ -z "${CUSER:-}" ]; then CUSER=$(stat -f %Su /dev/console 2> /dev/null); fi
if [ -z "${CUID:-}" ] && [ -n "$CUSER" ]; then CUID=$(id -u "$CUSER" 2> /dev/null); fi
TMPD="$O/.tmp"
mkdir -p "$TMPD"
KIT=${KIT:-}

# --- helpers (bounded/as_user/result as in behaviours.sh) ------------------------------------------------------

BN=0
bounded() {   # uses only _b* names: sh has no locals, and callers (soak) keep their own _secs
  _bsecs=$1; shift
  BN=$((BN + 1))
  _mark="$TMPD/timeout.$$.$BN"
  rm -f "$_mark"
  "$@" &
  _pid=$!
  ( sleep "$_bsecs"
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
as_user() { launchctl asuser "$CUID" sudo -u "$CUSER" "$@"; }
have_user() { [ -n "$CUSER" ] && [ -n "$CUID" ] && [ "$CUSER" != root ] && [ "$CUID" != 0 ]; }
oneline() { tr '\n\t' '  ' | tr -s ' ' | sed 's/^ //; s/ $//' | cut -c1-400; }
RESULTED=0
result() { printf 'HAOCHECK CHECK %s %s %s issue=%s\n' "$1" "$2" "$(printf '%s' "$3" | oneline)" "$4"; RESULTED=1; }
run_check() {
  RESULTED=0
  "$2"
  [ "$RESULTED" = 1 ] || result "$1" FAIL "the check printed no result (script error)" "$3"
}

REPORT_DIRS="/Library/Logs/DiagnosticReports"
[ -n "$CUSER" ] && REPORT_DIRS="$REPORT_DIRS /Users/$CUSER/Library/Logs/DiagnosticReports"

# reports FILE: list every crash report now (one path per line) into FILE.
reports() {
  : > "$1"
  for dd in $REPORT_DIRS; do
    [ -d "$dd" ] && bounded 30 find "$dd" -type f \( -name '*.ips' -o -name '*.panic' \) >> "$1" 2> /dev/null
  done
  sort -o "$1" "$1"
}
# new_reports BEFORE REGEX: names of reports written since BEFORE whose file name matches REGEX (case-insensitive).
new_reports() {
  reports "$TMPD/after"
  comm -13 "$1" "$TMPD/after" | sed 's|.*/||' | grep -i -E "$2" | tr '\n' ' ' | sed 's/ $//'
}
# boot_reports REGEX: names of reports written since boot whose file name matches REGEX.
BOOT=$(sysctl -n kern.boottime 2> /dev/null | sed -n 's/.*sec = \([0-9]*\),.*/\1/p')
boot_reports() {
  [ -n "$BOOT" ] || return 0
  touch -t "$(date -r "$BOOT" +%Y%m%d%H%M.%S)" "$TMPD/boot-ref"
  for dd in $REPORT_DIRS; do
    [ -d "$dd" ] && bounded 30 find "$dd" -type f \( -name '*.ips' -o -name '*.panic' \) -newer "$TMPD/boot-ref" 2> /dev/null
  done | sed 's|.*/||' | grep -i -E "$1" | tr '\n' ' ' | sed 's/ $//'
}
# upid NAME: the console user's newest process named exactly NAME.
upid() { pgrep -n -U "$CUID" -x "$1" 2> /dev/null; }
cpu() { ps -o %cpu= -p "$1" 2> /dev/null | tr -d ' '; }
# launch APP-NAME PROC [ARG...]: open the app (or a document/URL with it) as the console user; prints the pid or nothing.
# Notes whether it was already running so quit() leaves a pre-existing process alone.
launch() {
  _app=$1; _proc=$2; shift 2
  PRE=$(upid "$_proc")
  if [ $# -gt 0 ]; then bounded 30 as_user open -a "$_app" "$@" > /dev/null 2>&1
  else bounded 30 as_user open -a "$_app" > /dev/null 2>&1; fi
  _i=0
  while [ $_i -lt 20 ]; do _p=$(upid "$_proc"); [ -n "$_p" ] && { echo "$_p"; return 0; }; sleep 1; _i=$((_i + 1)); done
  return 0
}
quit() { [ -n "$1" ] && [ "$1" != "$PRE" ] && { kill -TERM "$1" 2> /dev/null; sleep 3; kill -KILL "$1" 2> /dev/null; }; return 0; }
kit_file() { [ -n "$KIT" ] && [ -e "$KIT/$1" ] && echo "$KIT/$1"; }

# soak NAME ISSUE APP PROC SECS CRASH-REGEX [ARG...]: open, stay up SECS, no new matching report, then quit.
soak() {
  _n=$1; _iss=$2; _app=$3; _proc=$4; _secs=$5; _re=$6; shift 6
  SOAK=SKIP; SOAK_D="no console user"; SOAK_CPU=""
  have_user || return 0
  reports "$TMPD/before"
  _p=$(launch "$_app" "$_proc" "$@")
  if [ -z "$_p" ]; then
    _c=$(new_reports "$TMPD/before" "$_re")
    SOAK=FAIL; SOAK_D="$_app did not start within 20 s${_c:+; reports: $_c}"; return 0
  fi
  sleep "$_secs"
  _alive=$(ps -p "$_p" -o pid= 2> /dev/null | tr -d ' ')
  SOAK_CPU=$(cpu "$_p")
  _c=$(new_reports "$TMPD/before" "$_re")
  quit "$_p"
  SOAK_D="$_app pid $_p, ${_secs} s, cpu ${SOAK_CPU:-?}%"
  if [ -n "$_c" ]; then SOAK=FAIL; SOAK_D="$SOAK_D; new reports: $_c"
  elif [ -z "$_alive" ]; then SOAK=FAIL; SOAK_D="$SOAK_D; exited before ${_secs} s"
  else SOAK=PASS; fi
}


# --- GUI primitives (dev image only; the test machine's GUI helper and perf probe are installed first) -----------
# gui ACTION [KEY=VALUE...]: one request to the GUI helper's mailbox (terminal-mac guest: screenshot, click x y
# [button=right], move x y, key key=COMBO, type text=...). Prints the reply; returns 0 when ok. Points: 1280x800.
GX=/private/var/db/hao-gui/exchange
PROBE=/private/var/db/hao-perf/perfprobe
gui_ok() { [ -d "$GX" ] && pgrep -f terminal-mac-guest > /dev/null 2>&1; }
gui() {
  _a=$1; shift
  _id=$(uuidgen | tr a-z A-Z)
  _dl=$(( $(date +%s) - 978307200 + 15 ))
  _args=""
  for kv in "$@"; do _k=${kv%%=*}; _v=$(printf '%s' "${kv#*=}" | sed 's/\\/\\\\/g; s/"/\\"/g'); _args="$_args${_args:+,}\"$_k\":\"$_v\""; done
  printf '{"id":"%s","action":"%s","arguments":{%s},"deadline":%s}' "$_id" "$_a" "$_args" "$_dl" > "$GX/.req-$_id"
  mv "$GX/.req-$_id" "$GX/request-$_id.json"
  _i=0
  while [ $_i -lt 150 ]; do
    if [ -f "$GX/reply-$_id.json" ]; then
      cat "$GX/reply-$_id.json"; _ok=$(plutil -extract ok raw -o - "$GX/reply-$_id.json" 2> /dev/null)
      rm -f "$GX/reply-$_id.json" "$GX/request-$_id.json"; [ "$_ok" = true ]; return
    fi
    sleep 0.1; _i=$((_i + 1))
  done
  rm -f "$GX/request-$_id.json"; return 124
}
# shot NAME: a screenshot into $O/NAME.png (prints the path).
shot() {
  bounded 5 caffeinate -u -t 1 > /dev/null 2>&1
  _r=$(gui screenshot) || return 1
  _img=$(printf '%s' "$_r" | sed -n 's/.*"image" *: *"\([^"]*\)".*/\1/p')
  [ -n "$_img" ] && mv "$GX/$_img" "$O/$1.png" && echo "$O/$1.png"
}
# wins FILE: the on-screen window list (window pid proc owner layer x y w h title, TSV) from bench's probe, run as a
# one-shot job in the console user's session (as laptop-perf windows does).
wins() {
  : > "$1"
  [ -x "$PROBE" ] || return 1
  _L=dev.hao.diagwin.$$; _G=${PROBE%/*}; _P=$_G/$_L.plist; _o=$_G/$_L.out
  printf '<?xml version="1.0" encoding="UTF-8"?><plist version="1.0"><dict><key>Label</key><string>%s</string><key>ProgramArguments</key><array><string>%s</string><string>windows</string></array><key>RunAtLoad</key><true/><key>KeepAlive</key><false/><key>StandardOutPath</key><string>%s</string><key>StandardErrorPath</key><string>%s</string></dict></plist>' "$_L" "$PROBE" "$_o" "$_o" > "$_P"
  : > "$_o"; chown "$CUID" "$_o"; chmod 644 "$_P" "$_o"
  launchctl bootstrap "gui/$CUID" "$_P" 2> /dev/null || { rm -f "$_P" "$_o"; return 1; }
  _i=0; sleep 1
  while [ $_i -lt 50 ] && launchctl print "gui/$CUID/$_L" 2> /dev/null | grep -q 'state = running'; do sleep 0.2; _i=$((_i + 1)); done
  cp "$_o" "$1"; launchctl bootout "gui/$CUID/$_L" 2> /dev/null; rm -f "$_P" "$_o"
  grep -q . "$1"
}
# win_of FILE OWNER [MINW MINH]: the first on-screen window line of OWNER (owner name column) at least MINW x MINH.
win_of() { awk -F'\t' -v o="$2" -v mw="${3:-40}" -v mh="${4:-40}" 'NR>1 && $4==o && $8>=mw && $9>=mh { print; exit }' "$1"; }

# px_stats PNG X Y W H: mean brightness and mean saturation (max-min of R,G,B, 0-255) of a screen region, from a 16x16
# downscale (sips crop + resize to BMP, read with od). Prints "bright sat" or nothing. Points == pixels on the 9,2.
px_stats() {
  _png=$1; _t=$TMPD/px.$$
  _W=$(sips -g pixelWidth "$_png" 2> /dev/null | awk '/pixelWidth/ { print $2 }'); _H=$(sips -g pixelHeight "$_png" 2> /dev/null | awk '/pixelHeight/ { print $2 }')
  [ -n "$_W" ] && [ -n "$_H" ] || return 1
  # the whole image scaled to 128x80 (no crop: sips' crop offset is unreliable), then the region in scaled cells
  bounded 20 sips -z 80 128 -s format bmp "$_png" --out "$_t.bmp" > /dev/null 2>&1 || return 1
  od -An -tu1 -v "$_t.bmp" | awk -v X="$2" -v Y="$3" -v RW="$4" -v RH="$5" -v W="$_W" -v H="$_H" '{ for (i = 1; i <= NF; i++) b[n++] = $i }
    END { off = b[10] + 256*b[11] + 65536*b[12]; w = b[18] + 256*b[19]; h = b[22] + 256*b[23]; td = 0
          if (h > 32767) { h = 65536 - h; td = 1 }
          bp = b[28] / 8; if (bp < 3 || w < 1 || h < 1) exit 1
          x0 = int(X * w / W); x1 = int((X + RW) * w / W); y0 = int(Y * h / H); y1 = int((Y + RH) * h / H)
          if (x1 > w) x1 = w; if (y1 > h) y1 = h; if (x1 <= x0 || y1 <= y0) exit 1
          row = int((w * bp + 3) / 4) * 4; sb = 0; ss = 0; k = 0
          for (y = y0; y < y1; y++) for (x = x0; x < x1; x++) { r = td ? y : h - 1 - y; o = off + r * row + x * bp
            B = b[o]; G = b[o+1]; R = b[o+2]
            mx = R; if (G > mx) mx = G; if (B > mx) mx = B; mn = R; if (G < mn) mn = G; if (B < mn) mn = B
            sb += (R + G + B) / 3; ss += mx - mn; k++ }
          printf "%d %d\n", sb / k, ss / k }'
  rm -f "$_t.bmp"
}
GUI_WHY0="needs the GUI helper and the perf probe on the dev image"
GUI_WHY=$GUI_WHY0
# The desktop checks need the desktop: while Setup Assistant shows a window (post-login panes), the menu bar is hidden and
# app windows sit behind it, so window-list checks would fail falsely (bench runs 11-18 on 8fb92d5, option C, C2).
gui_ready() { GUI_WHY=$GUI_WHY0
              have_user && gui_ok && [ -x "$PROBE" ] || return 1
              wins "$TMPD/wm-pre"
              if awk -F'\t' 'NR>1 && $4=="Setup Assistant" && $5==0 && $8>0 {f=1} END {exit !f}' "$TMPD/wm-pre"; then
                GUI_WHY="Setup Assistant is on screen: the desktop was not reached (hao-check must finish Setup first)"; return 1; fi; }

skip() { result "$1" SKIP "$3 (row in checks/regression.tsv)" "$2"; }

# Load gate (pm 11-18, after C3's first-login load slowed apps): the app rows wait until the 1-minute load average is below
# the CPU count, up to 300 s, and record the wait. Not a regression signal: a SKIP line says the rows ran under load.
load_gate() { _n=$(sysctl -n hw.ncpu 2>/dev/null || echo 4); _t0=$(date +%s)
  while :; do _l=$(sysctl -n vm.loadavg 2>/dev/null | awk '{print $2}')
    awk -v l="${_l:-0}" -v n="$_n" 'BEGIN {exit !(l < n)}' && { _w=$(( $(date +%s) - _t0 ))
      result L0-load-gate PASS "load ${_l} < ${_n} CPUs after ${_w} s wait" ''; return; }
    [ $(( $(date +%s) - _t0 )) -ge 300 ] && { result L0-load-gate SKIP "load still ${_l} (${_n} CPUs) after 300 s; app rows run under load" ''; return; }
    sleep 10; done; }

# --- checks ----------------------------------------------------------------------------------------------------

r9()  { soak R9-weather '#9' Weather Weather 20 'weather|mtlcompilerservice'
        result R9-weather "$SOAK" "$SOAK_D; map layers not switched (GUI)" '#9'; }
r11() { soak R11-freeform '#11' Freeform Freeform 60 'freeform'
        result R11-freeform "$SOAK" "$SOAK_D; board edit/save needs GUI driving" '#11'; }
r12() { c=$(boot_reports 'wallpaperagent')
        if [ -n "$c" ]; then result R12-wallpaperagent FAIL "since boot: $c" '#12'
        else result R12-wallpaperagent PASS "no WallpaperAgent report since boot" '#12'; fi; }
r15() { f=$(kit_file book.epub)
        if [ -n "$f" ]; then soak R15-books '#15' Books Books 40 '^books' "$f"
        else soak R15-books '#15' Books Books 40 '^books'; fi
        bounded 60 log show --last 2m --style compact --predicate 'process == "Books"' > "$O/books-log.txt" 2>&1
        u=$(grep -i -E 'unresolved' "$O/books-log.txt" | grep -i -c blissreader)
        [ "$u" -gt 0 ] && { SOAK=FAIL; SOAK_D="$SOAK_D; $u unresolved-import lines for BlissReader"; }
        result R15-books "$SOAK" "$SOAK_D${f:+; opened $f}" '#15'; }
r16() { c=$(boot_reports 'engagementcollector|bookassetd|booksnotification|bookdatastored')
        if [ -n "$c" ]; then result R16-books-helpers FAIL "since boot: $c" '#16'
        else result R16-books-helpers PASS "no Books helper report since boot (after R15's launch)" '#16'; fi; }
r18() { soak R18-gamed '#18' Chess Chess 60 'gamed'
        result R18-gamed "$SOAK" "$SOAK_D; gamed checked for new reports" '#18'; }
r20() { f=$(kit_file float16); [ -n "$f" ] || { skip R20-float16 '#20' "no \$KIT/float16"; return; }
        out=$(bounded 30 "$f" 2>&1); rc=$?; echo "$out" > "$O/float16.txt"
        if [ "$rc" = 0 ]; then result R20-float16 PASS "rc 0: $(echo "$out" | tail -n 1)" '#20'
        else result R20-float16 FAIL "rc $rc: $(echo "$out" | tail -n 1)" '#20'; fi; }
r22() { f=$(kit_file cubemap.scn); [ -n "$f" ] || { skip R22-scenekit-ql '#22' "no \$KIT/cubemap.scn"; return; }
        reports "$TMPD/before"
        bounded 60 as_user qlmanage -t -s 512 -o "$TMPD" "$f" > "$O/qlmanage.txt" 2>&1; rc=$?
        c=$(new_reports "$TMPD/before" 'mtlcompilerservice|quicklook|qlmanage')
        png=$(ls "$TMPD"/*.png 2> /dev/null | head -n 1); [ -n "$png" ] && cp "$png" "$O/scn-thumbnail.png"
        if [ -z "$c" ] && [ "$rc" = 0 ] && [ -n "$png" ]; then result R22-scenekit-ql PASS "thumbnail rendered" '#22'
        else result R22-scenekit-ql FAIL "qlmanage rc $rc, thumbnail $( [ -n "$png" ] && echo made || echo missing)${c:+; reports: $c}" '#22'; fi; }
r27() { have_user || { result R27-spotlight-apps SKIP "no console user" '#27'; return; }
        miss=""
        for a in Calculator Safari Notes; do
          hit=$(bounded 20 as_user mdfind "kMDItemContentType == com.apple.application-bundle && kMDItemFSName == '$a.app'" 2> /dev/null | head -n 1)
          echo "$a: ${hit:-none}" >> "$O/spotlight-apps.txt"; [ -n "$hit" ] || miss="$miss $a"
        done
        if [ -z "$miss" ]; then result R27-spotlight-apps PASS "Calculator, Safari, Notes found as apps" '#27'
        else result R27-spotlight-apps FAIL "not found as apps:$miss" '#27'; fi; }
r28() { have_user || { result R28-maps-ll SKIP "no console user" '#28'; return; }
        reports "$TMPD/before"; PRE=$(upid Maps)
        bounded 30 as_user open 'maps://?ll=48.8584,2.2945&z=15' > /dev/null 2>&1; sleep 20
        p=$(upid Maps); c=$(new_reports "$TMPD/before" '^maps'); quit "$p"
        if [ -n "$c" ]; then result R28-maps-ll FAIL "reports: $c" '#28'
        elif [ -z "$p" ]; then result R28-maps-ll FAIL "Maps did not open the link" '#28'
        else result R28-maps-ll PASS "Maps opened the ll= link, no crash; centring needs a screenshot check" '#28'; fi; }
r39() { have_user || { result R39-finder SKIP "no console user" '#39'; return; }
        c=$(boot_reports '^finder'); p=$(upid Finder)
        if [ -n "$c" ]; then result R39-finder FAIL "since boot: $c" '#39'
        elif [ -z "$p" ]; then result R39-finder FAIL "Finder not running" '#39'
        else result R39-finder PASS "Finder pid $p, no Finder report since boot (window use: app sweep)" '#39'; fi; }
r42() { have_user || { result R42-mds SKIP "no console user" '#42'; return; }
        reports "$TMPD/before"; t=0
        for q in txt Calculator txt; do bounded 30 as_user mdfind "$q" > /dev/null 2>&1 || t=$((t + 1)); done
        c=$(new_reports "$TMPD/before" '^mds')
        if [ -n "$c" ]; then result R42-mds FAIL "reports: $c" '#42'
        elif [ "$t" -gt 0 ]; then result R42-mds FAIL "$t of 3 searches failed or took over 30 s" '#42'
        else result R42-mds PASS "3 searches, no mds report" '#42'; fi; }
r43() { have_user || { result R43-dock SKIP "no console user" '#43'; return; }
        c=$(boot_reports '^dock-'); p=$(upid Dock)
        d="Dock pid ${p:-none}; display changes need GUI driving"
        if [ -n "$c" ]; then result R43-dock FAIL "$d; since boot: $c" '#43'
        elif [ -z "$p" ]; then result R43-dock FAIL "$d" '#43'
        else result R43-dock PASS "$d; no Dock report since boot" '#43'; fi; }
r45() { have_user || { result R45-bt-settings SKIP "no console user" '#45'; return; }
        reports "$TMPD/before"; PRE=$(upid "System Settings")
        bounded 30 as_user open 'x-apple.systempreferences:com.apple.BluetoothSettings' > /dev/null 2>&1; sleep 30
        p=$(upid "System Settings"); c=$(new_reports "$TMPD/before" 'bluetooth|system ?settings'); quit "$p"
        if [ -n "$c" ]; then result R45-bt-settings FAIL "reports: $c" '#45'
        elif [ -z "$p" ]; then result R45-bt-settings FAIL "System Settings did not stay open" '#45'
        else result R45-bt-settings PASS "Bluetooth pane open 30 s, no report" '#45'; fi; }
r48() { gui_ready || { soak R48-tv '#48' TV TV 30 '^tv'; result R48-tv "$SOAK" "$SOAK_D; veil not judged: $GUI_WHY" '#48'; return; }
        reports "$TMPD/before"; p=$(launch TV TV); worst=""; fails=0
        [ -n "$p" ] || { result R48-tv FAIL "TV did not start" '#48'; return; }
        for k in 1 2 3; do
          sleep 30; wins "$TMPD/wt"; w=$(win_of "$TMPD/wt" TV 400 300); f=$(shot "tv-$k")
          if [ -z "$w" ] || [ -z "$f" ]; then fails=$((fails + 1)); worst="$worst run$k:no-window"; continue; fi
          st=$(px_stats "$f" "$(echo "$w" | cut -f6)" "$(( $(echo "$w" | cut -f7) + 60 ))" "$(echo "$w" | cut -f8)" "$(( $(echo "$w" | cut -f9) / 2 ))")
          sat=${st#* }
          # the veil (#48) is a flat grey over the featured area: mean saturation under 12 (inferred threshold)
          if [ -z "$st" ]; then worst="$worst run$k:stats-failed"; fails=$((fails + 1))
          elif [ "$sat" -lt 12 ]; then worst="$worst run$k:grey(bright,sat=$st)"; fails=$((fails + 1))
          else worst="$worst run$k:ok($st)"; fi
          if [ $k -lt 3 ]; then quit "$p"; sleep 3; p=$(launch TV TV); fi
        done
        c=$(new_reports "$TMPD/before" '^tv'); quit "$p"
        if [ -n "$c" ]; then result R48-tv FAIL "reports: $c" '#48'
        elif [ $fails -gt 0 ]; then result R48-tv FAIL "$fails of 3 launches grey or without a window:$worst; $O/tv-*.png" '#48'
        else result R48-tv PASS "3 launches, featured area in colour:$worst" '#48'; fi; }
icloud() { [ -n "$CUSER" ] && bounded 10 defaults read "/Users/$CUSER/Library/Preferences/MobileMeAccounts" Accounts 2> /dev/null | grep -q AccountID; }
r49() { icloud || { result R49-findmy-locate SKIP "no Apple Account signed in" '#49'; return; }
        c=$(boot_reports 'findmylocateagent')
        if [ -n "$c" ]; then result R49-findmy-locate FAIL "since boot: $c" '#49'; else result R49-findmy-locate PASS "no report since boot" '#49'; fi; }
r50() { icloud || { result R50-findmy-search SKIP "no Apple Account signed in" '#50'; return; }
        c=$(boot_reports 'searchpartyuseragent')
        if [ -n "$c" ]; then result R50-findmy-search FAIL "since boot: $c" '#50'; else result R50-findmy-search PASS "no report since boot" '#50'; fi; }
r52() { soak R52-music '#52' Music Music 60 '^music'
        if [ "$SOAK" = PASS ] && [ "${SOAK_CPU%%.*}" -ge 20 ] 2> /dev/null; then SOAK=FAIL; SOAK_D="$SOAK_D (busy: over 20% at 60 s)"; fi
        result R52-music "$SOAK" "$SOAK_D" '#52'; }
r78() { hao=/usr/libexec/hao
        [ -x "$hao" ] || { result R78-auto-aot FAIL "no $hao" '#78'; return; }
        bounded 300 "$hao" --aot --check > "$O/aot-check.txt" 2>&1; rc=$?
        done_f=/private/var/db/hao/auto-aot.done
        sum=$(grep -E '[0-9]+ valid, [0-9]+ missing, [0-9]+ stale' "$O/aot-check.txt" | tail -n 1 | sed 's/^hao-aot: //')
        # #78 is "AOT runs by itself": pass when the run finished and nothing is missing or stale; a "not checkable"
        # entry (a native-only program on the list) does not count against it, but is named in the detail
        d="rc $rc; ${sum:-no summary}; $( [ -f $done_f ] && echo "auto-aot.done present" || echo "auto-aot.done missing")"
        if [ -f "$done_f" ] && echo "$sum" | grep -q ' 0 missing, 0 stale'; then result R78-auto-aot PASS "$d" '#78'
        else result R78-auto-aot FAIL "$d" '#78'; fi; }

r8() { gui_ready || { result R8-cc-sound-hover SKIP "$GUI_WHY" '#8'; result R10-cc-modules SKIP "$GUI_WHY" '#10'; return; }
       reports "$TMPD/before"; wins "$TMPD/w0"
       # Control Center owns the menu-bar status items; the clock is the rightmost, the Control Center item next to it.
       it=$(awk -F'\t' 'NR>1 && $4=="Control Center" && $5==25 && $7<30 { print $6+$8/2, $7+$9/2 }' "$TMPD/w0" | sort -n | tail -n 2 | head -n 1)
       [ -n "$it" ] || { result R8-cc-sound-hover FAIL "no Control Center status item in the window list" '#8'; result R10-cc-modules FAIL "no Control Center status item" '#10'; return; }
       x=${it% *}; y=${it#* }
       gui click "x=$x" "y=$y" > /dev/null; sleep 2; wins "$TMPD/w1"
       pan=$(awk -F'\t' 'NR>1 && $4=="Control Center" && $5!=25 && $8>=200 && $9>=150 { print; exit }' "$TMPD/w1")
       shot cc-panel > /dev/null
       if [ -z "$pan" ]; then
         result R10-cc-modules FAIL "the panel did not open (clicked the item at $x,$y); $O/cc-panel.png" '#10'
         result R8-cc-sound-hover FAIL "the panel did not open, hover not done" '#8'; gui key key=escape > /dev/null; return
       fi
       px=$(echo "$pan" | cut -f6); py=$(echo "$pan" | cut -f7); pw=$(echo "$pan" | cut -f8); ph=$(echo "$pan" | cut -f9)
       # an empty panel (#10) is a strip; with its modules it is at least ~300 points high (inferred from 26.x/27 layouts)
       if [ "${ph%.*}" -ge 300 ]; then result R10-cc-modules PASS "panel ${pw}x${ph} at $px,$py; $O/cc-panel.png" '#10'
       else result R10-cc-modules FAIL "panel only ${pw}x${ph} (no modules?); $O/cc-panel.png" '#10'; fi
       # hover every module: a 4x5 grid over the panel, 0.5 s per point (the Sound module is among them)
       for fy in 1 3 5 7 9; do for fx in 1 3 5 7; do
         gui move "x=$((px + pw * fx / 8))" "y=$((py + ph * fy / 10))" > /dev/null; sleep 0.5
       done; done
       sleep 3; c=$(new_reports "$TMPD/before" 'controlcenter'); gui key key=escape > /dev/null
       if [ -n "$c" ]; then result R8-cc-sound-hover FAIL "reports: $c" '#8'
       else result R8-cc-sound-hover PASS "20 hover points over the panel, no ControlCenter report" '#8'; fi; }
r37() { gui_ready || { result R37-menubar-dock SKIP "$GUI_WHY" '#37'; return; }
        wins "$TMPD/wm"; shot menubar-dock > /dev/null
        # menu-bar status items are layer-25 windows near the top; the Dock owns at least one window
        mb=$(awk -F'\t' 'NR>1 && $5==25 && $7<30' "$TMPD/wm" | wc -l | tr -d ' ')
        dk=$(awk -F'\t' 'NR>1 && $4=="Dock"' "$TMPD/wm" | wc -l | tr -d ' ')
        if [ "$mb" -ge 1 ] && [ "$dk" -ge 1 ]; then result R37-menubar-dock PASS "$mb menu-bar items, $dk Dock windows" '#37'
        else result R37-menubar-dock FAIL "menu-bar items $mb, Dock windows $dk; $O/menubar-dock.png" '#37'; fi; }
r29() { gui_ready || { result R29-freeform-board SKIP "$GUI_WHY" '#29'; return; }
        reports "$TMPD/before"; p=$(launch Freeform Freeform)
        [ -n "$p" ] || { result R29-freeform-board FAIL "Freeform did not start" '#29'; return; }
        sleep 8; gui key key=escape > /dev/null; sleep 1; gui key key=cmd+n > /dev/null; sleep 6; wins "$TMPD/wf"
        w=$(win_of "$TMPD/wf" Freeform 300 200); shot freeform-board > /dev/null
        gui key key=cmd+w > /dev/null; sleep 2; c=$(new_reports "$TMPD/before" 'freeform'); quit "$p"
        if [ -n "$c" ]; then result R29-freeform-board FAIL "reports: $c" '#29'
        elif [ -z "$w" ]; then result R29-freeform-board FAIL "no board window after Cmd-N; $O/freeform-board.png" '#29'
        else result R29-freeform-board PASS "new board window '$(echo "$w" | cut -f10)' $(echo "$w" | cut -f8)x$(echo "$w" | cut -f9)" '#29'; fi; }
r40() { gui_ready || { result R40-apps-drawer SKIP "$GUI_WHY" '#40'; return; }
        app=$(ls -d /System/Applications/Apps.app /Applications/Apps.app 2> /dev/null | head -n 1)
        [ -n "$app" ] || { result R40-apps-drawer FAIL "no Apps.app on the image" '#40'; return; }
        reports "$TMPD/before"; bounded 30 as_user open "$app" > /dev/null 2>&1; sleep 5; wins "$TMPD/wa"
        w=$(awk -F'\t' 'NR>1 && ($4=="Apps" || $3=="Apps") && $8>=200 && $9>=150 { print; exit }' "$TMPD/wa"); shot apps-drawer > /dev/null
        gui key key=escape > /dev/null; c=$(new_reports "$TMPD/before" '^apps|dock')
        if [ -n "$c" ]; then result R40-apps-drawer FAIL "reports: $c" '#40'
        elif [ -z "$w" ]; then result R40-apps-drawer FAIL "no Apps window within 5 s; $O/apps-drawer.png" '#40'
        else result R40-apps-drawer PASS "Apps window $(echo "$w" | cut -f8)x$(echo "$w" | cut -f9)" '#40'; fi; }
r53() { gui_ready || { result R53-dock-menu SKIP "$GUI_WHY" '#53'; return; }
        wins "$TMPD/wd"
        d=$(awk -F'\t' 'NR>1 && $4=="Dock" && $7>=600 && $8>=200 { print; exit }' "$TMPD/wd")
        [ -n "$d" ] || { result R53-dock-menu FAIL "no Dock window at the bottom of the screen" '#53'; return; }
        dx=$(echo "$d" | cut -f6); dy=$(echo "$d" | cut -f7); dw=$(echo "$d" | cut -f8); dh=$(echo "$d" | cut -f9)
        bad=""; n=0
        for f in 15 50 85; do
          cx=$((dx + dw * f / 100)); cy=$((dy + dh / 2))
          gui click "x=$cx" "y=$cy" button=right > /dev/null; sleep 1.5; wins "$TMPD/wm"; shot "dock-menu-$f" > /dev/null
          m=$(awk -F'\t' -v dy="$dy" 'NR>1 && $4=="Dock" && $5>=100 && $9>=40 && $7<dy { print; exit }' "$TMPD/wm")
          gui key key=escape > /dev/null; sleep 0.5
          if [ -z "$m" ]; then bad="$bad x$cx:no-menu"; continue; fi
          mx=$(echo "$m" | cut -f6); my=$(echo "$m" | cut -f7); mw=$(echo "$m" | cut -f8); mh=$(echo "$m" | cut -f9)
          bot=$((${my%.*} + ${mh%.*}))
          if [ "${my%.*}" -lt 22 ] || [ $bot -gt $((${dy%.*} + 5)) ] || [ $bot -lt $((${dy%.*} - 60)) ] || [ $cx -lt ${mx%.*} ] || [ $cx -gt $((${mx%.*} + ${mw%.*})) ]; then
            bad="$bad x$cx:menu-at-${mx},${my}-${mw}x${mh}"; else n=$((n + 1)); fi
        done
        if [ -z "$bad" ]; then result R53-dock-menu PASS "3 of 3 menus open directly above the clicked icon" '#53'
        else result R53-dock-menu FAIL "$n of 3 ok;$bad; screenshots $O/dock-menu-*.png" '#53'; fi; }

r36() { a=$(kit_file overlay-O-1); b=$(kit_file overlay-O-2)
        if [ -z "$a" ] || [ -z "$b" ]; then skip R36-overlay-tests '#36' "no \$KIT/overlay-O-1 and overlay-O-2 (runtime's -O overlay test programs; asked)"; return; fi
        bad=""; for t in "$a" "$b"; do bounded 60 as_user "$t" > "$O/$(basename "$t").txt" 2>&1 || bad="$bad $(basename "$t")=rc$?"; done
        if [ -z "$bad" ]; then result R36-overlay-tests PASS "both overlay programs exit 0 (Sound hover: R8)" '#36'
        else result R36-overlay-tests FAIL "failed:$bad" '#36'; fi; }

# --- run (order: passive checks first, then app checks; R16 after R15) -----------------------------------------

run_check R12-wallpaperagent r12 '#12'
run_check R39-finder r39 '#39'
run_check R43-dock r43 '#43'
run_check R49-findmy-locate r49 '#49'
run_check R50-findmy-search r50 '#50'
run_check R27-spotlight-apps r27 '#27'
run_check R42-mds r42 '#42'
run_check R78-auto-aot r78 '#78'
run_check R20-float16 r20 '#20'
run_check L0-load-gate load_gate ''
run_check R9-weather r9 '#9'
run_check R11-freeform r11 '#11'
run_check R15-books r15 '#15'
run_check R16-books-helpers r16 '#16'
run_check R18-gamed r18 '#18'
run_check R22-scenekit-ql r22 '#22'
run_check R28-maps-ll r28 '#28'
run_check R45-bt-settings r45 '#45'
run_check R48-tv r48 '#48'
run_check R52-music r52 '#52'
run_check R8-cc-sound-hover r8 '#8'
run_check R37-menubar-dock r37 '#37'
run_check R29-freeform-board r29 '#29'
run_check R40-apps-drawer r40 '#40'
run_check R53-dock-menu r53 '#53'
run_check R36-overlay-tests r36 '#36'
# Rows a script cannot judge (GUI hover/menus, screenshots, a person, a peer device, an account, the installer):
skip R31-lockscreen '#31' "needs a way to unlock without a password in the script (asked bench)"
skip R32-photos-zoom '#32' "needs a HEIC photo in the test account's library and per-frame capture during the zoom (asked bench)"
# #33 and #34 (the install path) are hao-accept's checks, not this image check.
skip R35-efi-boot '#35' "a person at the Option-key picker"
skip R46-appstore-buttons '#46' "blocked by #56 (App Store crashes first)"
skip R51-airdrop '#51' "needs a peer device"
skip R54-imessage '#54' "needs the owner's Apple Account and a peer"

rm -rf "$TMPD"
echo "HAOCHECK END diag-regressions"
