#!/bin/zsh
# /Library/Application Support/PerUserSeed/seed.zsh, run as root by local.peruser-seed (WatchPaths on the dslocal
# users dir + RunAtLoad). Revision "seed7" (2026-10-23, patchset/data/data.user-seed part seed-v7; source
# patchset/data/data.user-seed/src/seed-v7/seed.zsh). Previous: 026d2b64 ("seed6", 13b, peruser-seed-13/seed.zsh),
# 61814865 ("seed5", 12c), 4f691491 ("seed4", 11b), 43940acf ("seed3", 10d), 47f9fbd8 ("seedfix", 09d).
# Notes: setup-speed/RESULTS-12-all.md (wallpaper addendum), NOTES-12-seed.md, NOTES-11-seed.md, NOTES-10-seed.md;
# v7: patchset/data/data.user-seed/proposals/{wallpaper-carryover,wallpaper-bmp}.md,
# patchset/data/data.launchd-tuning/proposals/setup-waits.md (C-1).
# v7 changes (each new step runs only when its payload file is present, so each is its own patch part; without
# those files v7 behaves exactly like v6):
#   a. place_wp also places wallpaperexportd's export metadata (payload wallpaper/Metadata.plist, part
#      wallpaper-carryover) next to Wallpaper.png, metadata FIRST, so the new user's WallpaperAgent finds a complete
#      export ("Successfully read metadata") and does not re-export after the desktop appears (E-boot1: re-export at
#      desktop.ready -> storagekitd updatePreboot -> a 61 s efilogin-helper run). Creates /private/var/db/Wallpapers
#      (root:wheel 755) when it is missing (a Data volume without data.wallpaper-defaults' cache).
#   b. place_bmp (payloads wallpaper/aerial-still-1280x800.bmp + wpcache-name, part wallpaper-bmp): once the new
#      user's copy of the default aerial movie exists, has the expected size and a stable mtime, puts the aerials
#      extension's 1280x800 still-frame BMP into the user's com.apple.wallpaper.agent cache under the name the
#      extension looks up (wpcache-name: sha256(movie path)-1280-800-0-<movie mtime as CFAbsoluteTime bits>.bmp),
#      owner uid:gid 644, only into an existing cache dir that holds no .bmp yet. Never overwrites.
#   c. efilogin re-enable (flag file efilogin-reenable, part efilogin-reenable; pairs with
#      data.launchd-tuning/efilogin-setup-window, which ships com.apple.efilogin-helper disabled for the Setup
#      window). Runs BEFORE the build gate, so on every build: if the helper is disabled, it is re-enabled
#      (launchctl enable + bootstrap of Apple's plist) at once when an account with uid >= 501 older than NEWAGE
#      exists or the payload build does not match the OS (boot-time fallback); otherwise EFSETTLE s after gui/<uid>
#      exists for a new account (polled every 1 s; the desktop draws first), or EFWAIT s after the new account was seen, or when the run
#      ends, whichever comes first. A run never ends with the helper disabled while an account >= 501 exists. No
#      explicit updatePreboot.
#   Data only, no code change: the NotificationCenter and iconservicesagent Metal trees carry E-booted-setup1's own
#   cache files (more shader records), see the part's README.
# v6 change (13a run, user-driven 2026-10-16): after account creation Setup Assistant restarts in the new user's session,
#   its wallpaper snapshot times out (0.5 s) and it falls back to /var/db/Wallpapers/<GeneratedUID>/Wallpaper.png, which
#   does not exist yet -> DefaultDesktop.heic (other crop) -> the blurred background swaps to the aerial a pane later.
#   v6 places WallpaperAgent's own export of the default aerial (payload wallpaper/Wallpaper.png, the same file 13a puts
#   in the FFFF... caches; WallpaperAgent later writes byte-identical content there, verified ff839d89 on the laptop) at
#   that path as soon as the account is known to be new (13a run: 102 s before Setup Assistant read it). Never over an
#   existing directory (WallpaperAgent first = left alone). Everything else unchanged from v5.
# v5 change (11-all run, analysis-2026-10-16/NOTES-11all.md item 1; laptop tests in NOTES-12-seed.md): the poke spawned
#   passd "because ipc (mach)", but passd (POSIXSpawnType Adaptive) only stays out of the background band (priority 4)
#   while a client WAITS on a pending request to it: ~3 s with v4's single 3 s poke, and v4 also killed the poke when the
#   passd process appeared. Under the first-login load passd then sat in dyld at priority 4 for ~2 min. taskpolicy -B,
#   -t/-l and renice do NOT lift it (tested on an Adaptive launchd job). v5 CHAINS long pokes (passd-poke v2, WAIT
#   PKWAITS s each; tested: each pending poke re-boosts the job to 31, launchd drops the boost after ~12 s) until passd
#   answers (any reply/error = its listener ran) or PKHOLD s, and never kills the chain because the process appeared.
# v4 change (10-all run, analysis-2026-10-15/10all-run/NOTES-10all.md): passd is started ONLY by the XPC poke. v3's
#   `launchctl kickstart` spawned passd "because non-ipc demand" (POSIXSpawnType Adaptive, no importance boost): it sat
#   in dyld at base priority 4 for 2.5 min under load 600-740 and the Terms pane waited on it. The poke makes launchd
#   spawn it "because ipc (mach)", as in 07h/09-all (listener up ~6 s after the spawn). Nothing else changed.
# v3 changes (09-all run, analysis-2026-10-15/09all-run/NOTES-09d-seed-metal-hello.md, NOTES-09bc-setup.md):
#   - Metal trees: first-use trees first (FIRSTUSE below), then the rest; copied in chunks with ONE `cp -Rpc` (clones)
#     per chunk into a staging dir in the user's cache dir, one builtin chown -R, then builtin renames per tree. No
#     process per tree (v2: ~5 per tree, ~1 s each at load 800 -> 239 s and two first-use trees lost their race).
#   - per-tree placement times in ms in the log; trees found already present are named.
#   - users-dir signature from the zstat builtin (v2 forked stat between trees); sub-second waits via zselect.
#   - Apple Pay warm-up (replaces the 09c LaunchAgent, which pended in the new user's on-demand-only gui domain):
#     once gui/<uid> exists, `launchctl asuser <uid> passd-poke` sends one XPC message to com.apple.passd.library
#     (ipc demand launch, the way passd started early in 07h/09-all; v4: no kickstart). Asynchronous, time-bounded
#     (PKWAIT), never blocks the LS/icon/Metal work.
#
# For every NEW account (uid >= 501, accountPolicyData creationTime < NEWAGE s ago) it places, before that user's lsd
# starts (record -> lsd is ~11-17 s at account creation):
#   1. the LaunchServices store -> <DARWIN_USER_DIR>/com.apple.LaunchServices.dv/ (FIRST, renames of pre-made clones).
#      Right after the renames it looks for lsd(uid): an lsd that started BEFORE the placement keeps its own in-memory
#      DB and is killed (launchd respawns it on demand and the respawn loads the placed file); an lsd that started after
#      it is left alone. "Before" = running at the pre-placement check, or ps lstart second < placement second, or
#      (same second) a pid below the reference pid spawned right after the renames. Writes marker <uid>.ls.
#   2. the icon index (payload is/), as <DARWIN_USER_CACHE_DIR>/com.apple.iconservices (711/600).
#   3. Metal shader cache trees -> <DARWIN_USER_CACHE_DIR>/<bundle>, never over an existing tree (a tree that appears
#      between the check and the rename is detected and the app's copy kept). Writes marker <uid> (final).
#   4. the passd kick (above), armed when 1 succeeded for a new account.
#   0. (v6, first, as soon as the account is known to be new) the wallpaper cache /var/db/Wallpapers/<GeneratedUID>/
#      Wallpaper.png (root:wheel 755/644, as WallpaperAgent writes it), only when that directory does not exist.
# Old accounts are marked and left alone, so a late trigger never overwrites a used store or kills a used lsd.
# Multiple accounts: launchd coalesces WatchPaths events while the job runs, so the job does not exit until the users
# dir has been quiet for LINGER s and nothing is pending (accounts, Metal, passd kicks); it rescans when the dir changes.
# Owner = the user's uid and primary gid (id -g). No-op when the OS build differs from the payload's build file.
# Log (ms timestamps): /private/var/log/peruser-seed.log (plist StandardOut/ErrorPath). Markers: /private/var/db/peruser-seed/.
# zsh + POSIX tools only (the target has no python3).
setopt null_glob
zmodload zsh/datetime
zmodload -F zsh/files b:zf_mv b:zf_rm b:zf_mkdir b:zf_chown b:zf_chmod && zmodload -F zsh/stat b:zstat &&
    zmodload zsh/zselect || { print -r -- "$(date '+%F %T') [$$] zsh modules zsh/files, zsh/stat or zsh/zselect missing; nothing placed"; exit 0 }
export LC_ALL=C
SEED="/Library/Application Support/PerUserSeed"
DONE=/private/var/db/peruser-seed
USERS=/private/var/db/dslocal/nodes/Default/users
NEWAGE=600      # s: an account whose creationTime is older than this is not new
WAIT=90         # s: how long one account may stay "not ready" (no gid / user dirs / creationTime) in one run
LINGER=30       # s: keep rescanning until the users dir has been unchanged this long and nothing is pending
MAXRUN=600      # s: hard cap for one run
LSN=com.apple.LaunchServices-20971545-v2.csstore
CHUNK=8         # Metal trees per cp after the first-use group (users dir rescanned between chunks)
FIRSTUSE=(com.apple.iconservicesagent com.apple.notificationcenterui com.apple.SetupAssistant com.apple.loginwindow
          com.apple.dock.extra com.apple.Spotlight com.apple.controlcenter)
PKWAIT=240      # s: passd kick gives up if gui/<uid> has not appeared / passd is not up this long after the LS store (v5: 180 -> 240, room for PKHOLD after a late domain)
PKPOLL=50       # 1/100 s: the chain polls gui/<uid>/com.apple.passd this often (r7: in the chain; was 2 s in the main loop)
PKHOLD=90       # s: keep a boosting poke pending (chained) until passd answers, at most this long after the first poke
PKWAITS=10      # s: WAIT of each chained poke (passd-poke v2 arg 2); below launchd's ~12 s boost window
PASSD=com.apple.passd
POKE="$SEED/passd-poke"
POKESVC=com.apple.passd.library
WPSRC="$SEED/wallpaper/Wallpaper.png"   # v6: WallpaperAgent's export of the default aerial (= 13a's FFFF... caches)
WPMETA="$SEED/wallpaper/Metadata.plist" # v7a: wallpaperexportd's Metadata.plist for that export (349 B c895e93c)
WPDIR=/private/var/db/Wallpapers
BMSRC="$SEED/wallpaper/aerial-still-1280x800.bmp"   # v7b: the aerials extension's still of the default aerial
BMNAME="$SEED/wpcache-name"                          # v7b: prints the cache file name for a movie path
BMMOV="Library/Application Support/com.apple.wallpaper/aerials/videos/4C108785-A7BA-422E-9C79-B0129F1D5550.mov"
BMSIZE=469948225   # bytes of the default aerial the BMP was made from (data.wallpaper-defaults, ee04500a)
BMDIR="Library/Containers/com.apple.wallpaper.agent/Data/Library/Caches/com.apple.wallpaper.caches/extension-com.apple.wallpaper.extension.aerials"
BMWAIT=300         # s: give up on the BMP if the movie/cache dir is not ready this long after the LS store
EFFLAG="$SEED/efilogin-reenable"         # v7c: present = re-enable com.apple.efilogin-helper (see header c)
EFLABEL=com.apple.efilogin-helper
EFPLIST=/System/Library/LaunchDaemons/$EFLABEL.plist
EFWAIT=600         # s: re-enable at the latest this long after a new account was first seen
EFSETTLE=180       # s: and not before gui/<uid> has existed this long: the desktop draws first (BETA7 bar; at once, the helper took both cores while the desktop started, 2026-10-28)

log() { local e=$EPOCHREALTIME t; strftime -s t '%F %T' ${e%.*}; print -r -- "$t.${${e#*.}[1,3]} [$$] $*" }
hms() { local t f=000; strftime -s t '%T' ${1%.*}; [[ $1 == *.* ]] && f=${${1#*.}[1,3]}; REPLY="$t.$f" }   # epoch[.frac] -> HH:MM:SS.mmm
ms() { REPLY=$(( (EPOCHREALTIME - $1) * 1000 )); REPLY=${REPLY%.*} }        # ms since epoch[.frac] $1
dsig() { local -a a f; zstat -A a +mtime "$USERS" 2>/dev/null; f=("$USERS"/*); REPLY="${a[1]:-0}:${#f}" }   # no fork
nap() { zselect -t $1 2>/dev/null || : }                                       # $1 hundredths of a second, no fork
own() { zf_chown -R "$1" "$2" }                                                # (the offline mock replaces this line)

# ---- v7c: efilogin-helper re-enable (runs before the build gate) ---------------------------------------------------
EFST=          # '' = nothing to do, wait = disabled and waiting for a new account's gui domain, done
typeset -F EFDL=0 EFNEXT=0 EFGUI=0
ef_disabled() { launchctl print-disabled system 2>/dev/null | grep -Eq "\"$EFLABEL\" => (disabled|true)" }
ef_enable() {   # $1 why: enable + bootstrap Apple's job, then check that it is loaded
    local o1 o2 r1 r2
    o1=$(launchctl enable "system/$EFLABEL" 2>&1); r1=$?
    o2=$(launchctl bootstrap system "$EFPLIST" 2>&1); r2=$?     # rc != 0 when already loaded: decided by print below
    o1=${o1//$'\n'/ }; o2=${o2//$'\n'/ }
    if launchctl print "system/$EFLABEL" >/dev/null 2>&1; then
        log "efilogin-helper re-enabled (live): $1 (enable rc $r1${o1:+ $o1}; bootstrap rc $r2${o2:+ $o2})"
    else
        log "efilogin-helper FAILED: enabled for next boot only: $1 (enable rc $r1${o1:+ $o1}; bootstrap rc $r2${o2:+ $o2}; launchctl print system/$EFLABEL fails)"
    fi
    ef_disabled && log "efilogin-helper: still listed as disabled after launchctl enable"
    EFST=done
}
ef_boot() {   # every run, every build: re-enable now (fallback) or wait for a new account's gui domain
    [[ -e $EFFLAG ]] || return 0
    ef_disabled || { log "efilogin-helper is not disabled; nothing to re-enable"; return 0 }
    [[ $want == $have ]] || { ef_enable "boot fallback: seed build ${want:-none} != OS $have"; return 0 }
    local row u uid ct
    local -F age
    for row in ${(f)"$(dscl . -list /Users UniqueID 2>/dev/null)"}; do
        u=${row%% *}; uid=${row##* }
        [[ $uid == <-> ]] && (( uid >= 501 )) || continue
        ct=$(dscl . -read "/Users/$u" accountPolicyData 2>/dev/null | tail -n +2 | plutil -extract creationTime raw -o - - 2>/dev/null)
        [[ $ct == <->(|.<->) ]] || continue      # no creationTime yet (being created): the run's watcher covers it
        (( age = EPOCHREALTIME - ct ))
        (( age > NEWAGE )) && { ef_enable "boot fallback: $u ($uid) was created $(printf %.0f $age) s ago (> $NEWAGE s)"; return 0 }
    done
    EFST=wait; log "efilogin-helper is disabled (Setup window); it is re-enabled ${EFSETTLE} s after gui/<uid> appears for a new account"
}
ef_pass() {   # main loop: poll gui/<uid> of the accounts >= 501 every 1 s; deadline EFWAIT after the first one was seen
    [[ $EFST == wait ]] && (( ${#NAME} )) || return 0
    (( EPOCHREALTIME < EFNEXT )) && return 0
    EFNEXT=$(( EPOCHREALTIME + 1 ))
    (( EFDL > 0 )) || EFDL=$(( EPOCHREALTIME + EFWAIT ))
    local uid
    for uid in ${(k)NAME}; do
        launchctl print "gui/$uid" >/dev/null 2>&1 || continue
        (( EFGUI > 0 )) || { EFGUI=$EPOCHREALTIME; log "efilogin-helper: gui/$uid exists (${NAME[$uid]}); re-enable in ${EFSETTLE} s"; }
        (( EPOCHREALTIME - EFGUI >= EFSETTLE )) && { ef_enable "gui/$uid has existed ${EFSETTLE} s (${NAME[$uid]})"; return 0 }
        break
    done
    (( EPOCHREALTIME >= EFDL )) && ef_enable "no gui/<uid> domain ${EFWAIT} s after the account was seen"
    return 0
}
ef_final() {   # end of a run: never leave it disabled while an account >= 501 exists
    [[ $EFST == wait ]] || return 0
    if (( ${#NAME} )); then ef_enable "$1"; else log "efilogin-helper left disabled: no account with uid >= 501 yet ($1)"; fi
}

typeset -A NAME   # uid -> short name, accounts with uid >= 501 (filled by ls_pass)
mkdir -p $DONE && chmod 700 $DONE
want=; [[ -r "$SEED/build" ]] && want=$(<"$SEED/build"); have=$(sw_vers -buildVersion)
ef_boot
[[ -n $want ]] || { log "no payload build file; nothing placed"; exit 0 }
[[ $want == $have ]] || { log "seed build $want != OS $have, nothing placed"; exit 0 }

typeset -A FIRST NOTE WHY GAVE UGID CDIR LSAT NEWLOG MFAIL WPDONE WPLOG
typeset -A BMST BMDL BMHOME BMSIG BMSEEN BMWHY      # v7b aerial still per uid: state, deadline, home, movie size:mtime, since, reason
typeset -A PKDL PKST PKT PKNEXT PKPID PKOUT PKSEEN PKGUI   # passd kick per uid: deadline, state, state time, next poll, chain, output, passd pid seen, gui time
typeset -i PENDING=0
LASTPASS=0

note() {   # $1 uid, $2 reason key, $3 message: remember why an account is pending; log each reason once
    WHY[$1]=$2; [[ ${NOTE[$1]} == $3 ]] && return; NOTE[$1]=$3; log "$3"
}
place_wp() {   # $1 name $2 uid: wallpaper cache for a new account (v6). Once per run per uid; never over an existing dir
    local u=$1 uid=$2 g T
    setopt local_options extended_glob
    [[ -n ${WPDONE[$uid]} ]] && return 0
    [[ -s "$WPSRC" ]] || { WPDONE[$uid]=1; log "$u: no wallpaper in the payload; wallpaper step skipped"; return 0 }
    g=$(dscl . -read "/Users/$u" GeneratedUID 2>/dev/null | awk '/^GeneratedUID:/ {print $2}')
    [[ $g == [0-9A-F](#c8)-[0-9A-F](#c4)-[0-9A-F](#c4)-[0-9A-F](#c4)-[0-9A-F](#c12) ]] ||
        { [[ -n ${WPLOG[$uid]} ]] || { WPLOG[$uid]=1; log "$u ($uid): no GeneratedUID yet (got '$g'); wallpaper retried while the account is pending" }; return 0 }
    WPDONE[$uid]=1; T=$WPDIR/$g
    if [[ ! -d $WPDIR ]]; then   # v7: a Data volume without data.wallpaper-defaults' FFFF... caches has no Wallpapers dir
        if zf_mkdir -m 755 "$WPDIR" 2>/dev/null && zf_chown root:wheel "$WPDIR"; then log "$u: created $WPDIR (root:wheel 755)"
        else log "$u: $WPDIR missing and cannot be created; wallpaper step skipped"; return 0; fi
    fi
    [[ -e $T ]] && { log "$u: $T exists already (WallpaperAgent first); left alone"; return 0 }
    zf_mkdir -m 755 "$T" 2>/dev/null || { log "$u: $T appeared or mkdir failed; left alone"; return 0 }
    zf_chown root:wheel "$T"
    if [[ -s $WPMETA ]]; then    # v7a: metadata first, the picture last (whoever sees Wallpaper.png also sees the metadata)
        if cp "$WPMETA" "$T/.Metadata.plist.tmp" && zf_chmod 644 "$T/.Metadata.plist.tmp" && zf_chown root:wheel "$T/.Metadata.plist.tmp" &&
           zf_mv "$T/.Metadata.plist.tmp" "$T/Metadata.plist"; then
            log "$u: wallpaper export metadata placed $T/Metadata.plist"
        else
            zf_rm -f "$T/.Metadata.plist.tmp" "$T/Metadata.plist"; log "$u: FAILED to place $T/Metadata.plist (continuing with the picture only)"
        fi
    fi
    if cp "$WPSRC" "$T/.Wallpaper.png.tmp" && zf_chmod 644 "$T/.Wallpaper.png.tmp" && zf_chown root:wheel "$T/.Wallpaper.png.tmp" &&
       zf_mv "$T/.Wallpaper.png.tmp" "$T/Wallpaper.png"; then
        log "$u: wallpaper cache placed $T/Wallpaper.png"
    else
        zf_rm -f "$T/.Wallpaper.png.tmp" "$T/Metadata.plist"; log "$u: FAILED to place the wallpaper cache in $T"
    fi
    return 0
}

# ---- v7b: the aerials extension's still-frame BMP (asynchronous: waits for the user's copy of the movie) ----------
bm_arm() {   # $1 uid $2 name: after the LS store of a new account is placed
    [[ -s $BMSRC && -x $BMNAME ]] || return 0
    local h
    h=$(dscl . -read "/Users/$2" NFSHomeDirectory 2>/dev/null | sed -n 's/^NFSHomeDirectory: //p')
    [[ $h == /* && -d $h ]] || { log "$2 ($1): home folder not found ('$h'); aerial still step skipped"; return 0 }
    BMST[$1]=wait; BMDL[$1]=$(( EPOCHREALTIME + BMWAIT )); BMHOME[$1]=$h; BMSIG[$1]=; BMSEEN[$1]=0; BMWHY[$1]="armed"
}
bm_end() { unset "BMST[$1]" "BMDL[$1]" "BMHOME[$1]" "BMSIG[$1]" "BMSEEN[$1]" "BMWHY[$1]" }
bm_pass() {
    local uid u h V X n e
    local -a f
    local -A st
    setopt local_options extended_glob
    for uid in ${(k)BMST}; do
        u=${NAME[$uid]:-uid$uid}; h=${BMHOME[$uid]}; V="$h/$BMMOV"; X="$h/$BMDIR"
        if (( EPOCHREALTIME >= BMDL[$uid] )); then
            log "$u ($uid): aerial still not placed: not ready after ${BMWAIT} s (${BMWHY[$uid]})"; bm_end $uid; continue
        fi
        st=(); zstat -H st "$V" 2>/dev/null || { BMWHY[$uid]="no movie $BMMOV yet"; continue }
        n="${st[size]}:${st[mtime]}"
        if [[ $n != ${BMSIG[$uid]} ]]; then BMSIG[$uid]=$n; BMSEEN[$uid]=$EPOCHREALTIME; BMWHY[$uid]="movie changing ($n)"; continue; fi
        (( EPOCHREALTIME - BMSEEN[$uid] >= 2 )) || continue
        [[ ${st[size]} == $BMSIZE ]] || { log "$u ($uid): movie is ${st[size]} bytes, the still is for $BMSIZE; aerial still skipped"; bm_end $uid; continue }
        [[ -d $X ]] || { BMWHY[$uid]="no cache dir $BMDIR (or not accessible)"; continue }
        f=("$X"/*.bmp(N))
        (( ${#f} )) && { log "$u ($uid): aerials cache already holds ${#f} .bmp (${f[1]:t}); left alone"; bm_end $uid; continue }
        n=$("$BMNAME" "$V" 1280 800 0 2>&1)
        [[ $n == [0-9a-f](#c64)-1280-800-0-[0-9a-f](#c16).bmp ]] || { log "$u ($uid): wpcache-name failed ($n); aerial still skipped"; bm_end $uid; continue }
        if e=$(cp -c "$BMSRC" "$X/.$n.tmp" 2>&1 || cp "$BMSRC" "$X/.$n.tmp" 2>&1) &&
           zf_chown "$uid:${UGID[$uid]}" "$X/.$n.tmp" && zf_chmod 644 "$X/.$n.tmp" && zf_mv "$X/.$n.tmp" "$X/$n"; then
            ms ${LSAT[$uid]}; log "$u ($uid): aerial still placed $BMDIR/$n (+$REPLY ms after the LS store; movie mtime ${st[mtime]})"
        else
            zf_rm -f "$X/.$n.tmp"; log "$u ($uid): FAILED to place the aerial still in $X (${e//$'\n'/ })"
        fi
        bm_end $uid
    done
}
metal_todo() {   # REPLY = number of <uid>.ls markers this run can still work on
    local m; REPLY=0
    for m in $DONE/<->.ls; do [[ -n ${MFAIL[${${m:t}%.ls}]} ]] || (( ++REPLY )); done
}

# LS store placement + lsd check. $1 name $2 uid $3 gid $4 D. 0 placed, 1 failed (retry)
place_ls() {
    local u=$1 uid=$2 gid=$3 D=$4 V f p r0 r1 t0 t1 st se k why
    local -a L0 L1
    V=${D}com.apple.LaunchServices.dv
    zf_mkdir -p "$V" && zf_chown "$uid:$gid" "$V" && zf_chmod 700 "$V" || { log "$u: cannot create $V"; return 1 }
    for f in "$SEED"/ls/*; do   # clones first, so the swap-in is only renames
        zf_rm -f "$V/.${f:t}.tmp"
        cp -c "$f" "$V/.${f:t}.tmp" && zf_chown "$uid:$gid" "$V/.${f:t}.tmp" && zf_chmod 600 "$V/.${f:t}.tmp" ||
            { log "$u: FAILED to stage ${f:t}"; zf_rm -f "$V"/.*.tmp; return 1 }
    done
    [[ -e $V/$LSN ]] && log "$u: $LSN already exists before placement (an lsd saved first); replacing it"
    L0=($(pgrep -u "$uid" -x lsd))
    /usr/bin/true & r0=$!
    t0=$EPOCHREALTIME
    for f in "$SEED"/ls/*; do zf_mv -f "$V/.${f:t}.tmp" "$V/${f:t}" || log "$u: FAILED to place ${f:t}"; done
    t1=$EPOCHREALTIME
    /usr/bin/true & r1=$!
    L1=($(pgrep -u "$uid" -x lsd))
    LSAT[$uid]=$t1
    hms $t0; local h0=$REPLY; hms $t1
    log "$u ($uid): LS store placed $h0-$REPLY (ref pids $r0/$r1); lsd before: ${L0[*]:-none}, after: ${L1[*]:-none}"
    for p in $L1; do
        st=$(ps -o lstart= -p $p 2>/dev/null); st=(${=st}); st=${(j: :)st}; se=
        [[ -n $st ]] && strftime -r -s se '%a %b %d %T %Y' "$st" 2>/dev/null
        if (( ${L0[(Ie)$p]} )); then k=1; why="was running before the placement"
        elif [[ $se == <-> ]] && (( se < ${t0%.*} )); then k=1; why="started $st, before the placement second"
        elif [[ $se == <-> ]] && (( se > ${t1%.*} )); then k=0; why="started $st, after the placement second"
        elif (( p > r1 || r1 - p > 50000 )); then k=0; why="started $st (same second), pid > ref $r1: spawned after the placement"
        else k=1; why="started $st (same second), pid < ref $r1: spawned before the placement completed"
        fi
        if (( k )); then log "$u: lsd $p $why -> kill -9 (respawn loads the placed store)"; kill -9 $p
        else log "$u: lsd $p $why -> left running (it loaded the placed store)"; fi
    done
    return 0
}

# fontd's font registry (mac-image user-seed r6): setup-caches' account-neutral copy into <C>com.apple.FontRegistry,
# as fontd makes it (folder 700, file 644, the user's), before the user's fontd starts (record -> fontd ~18-24 s).
# Without it that fontd scans every font while Setup Assistant waits on it. Its user-folder row (domain 1) holds
# cache/font-registry's placeholder in fontd's "not seen yet" state; sqlite3 writes this account's <home>/Library/Fonts
# there (an UPDATE fires none of fontd's triggers). Never over a folder fontd made first.
FONTPH=/var/empty/Library/Fonts
place_fonts() {   # $1 name $2 uid $3 gid $4 C
    local u=$1 uid=$2 gid=$3 C=$4 R S h e
    R=${C}com.apple.FontRegistry; S=${C}.peruser-seed.fonts
    [[ -s "$SEED/fonts/fontregistry.user" ]] || { log "$u: no font registry in the payload; font step skipped"; return 0 }
    [[ -e $R ]] && { log "$u: $R exists already (fontd first); left alone"; return 0 }
    h=$(dscl . -read "/Users/$u" NFSHomeDirectory 2>/dev/null | sed -n 's/^NFSHomeDirectory: //p')
    [[ $h == /* ]] || { log "$u: no home folder in the record ('$h'); font step skipped"; return 0 }
    zf_rm -rf "$S"
    if zf_mkdir -m 700 "$S" && cp -c "$SEED/fonts/fontregistry.user" "$S/fontregistry.user" &&
       e=$(/usr/bin/sqlite3 "$S/fontregistry.user" "UPDATE dir_table SET path_column = '${h//\'/''}/Library/Fonts' WHERE domain_column = 1 AND path_column = '$FONTPH'; PRAGMA wal_checkpoint(TRUNCATE);" 2>&1) &&
       zf_rm -f "$S/fontregistry.user-wal" "$S/fontregistry.user-shm" &&
       zf_chmod 644 "$S/fontregistry.user" && own "$uid:$gid" "$S" && [[ ! -e $R ]] && zf_mv "$S" "$R"; then
        if [[ -e $R/${S:t} ]]; then zf_rm -rf "$R/${S:t}"; log "$u: fontd made $R during the copy; left alone"   # mv went inside
        else log "$u: font registry placed (user font folder $h/Library/Fonts)"; fi
    else
        zf_rm -rf "$S"; log "$u: font registry not placed ($R appeared, or copy/sqlite3 failed: ${e//$'\n'/ })"
    fi
}

# One account, phase 1 (decide new/old, LS store, icon index). 0 = no more phase-1 work, 1 = pending
try_ls() {
    local u=$1 uid=$2 gid ct D C I f
    local -F age
    [[ -n ${FIRST[$uid]} ]] || FIRST[$uid]=$EPOCHREALTIME
    if (( EPOCHREALTIME - FIRST[$uid] > WAIT )); then
        if [[ ${WHY[$uid]} == ct ]]; then   # cannot prove it is new: never seed it
            log "$u ($uid): no accountPolicyData creationTime after ${WAIT} s; age unknown, left alone"
            print -r "left alone: no creationTime" > "$DONE/$uid"; return 0
        fi
        GAVE[$uid]=1; log "$u ($uid): still not ready after ${WAIT} s (${NOTE[$uid]}); gave up for this run, the next users-dir change retries"
        return 0
    fi
    gid=$(id -g "$u" 2>/dev/null) || { note $uid gid "$u ($uid): no gid yet"; return 1 }
    ct=$(dscl . -read "/Users/$u" accountPolicyData 2>/dev/null | tail -n +2 | plutil -extract creationTime raw -o - - 2>/dev/null)
    [[ $ct == <->(|.<->) ]] || { note $uid ct "$u ($uid): no accountPolicyData creationTime yet"; return 1 }
    (( age = EPOCHREALTIME - ct ))
    hms $ct
    if (( age > NEWAGE )); then
        log "$u ($uid): created $(strftime '%F' ${ct%.*}) $REPLY ($(printf %.0f $age) s ago, accountPolicyData); not a new account, left alone"
        print -r "left alone: created $ct" > "$DONE/$uid"; return 0
    fi
    [[ -n ${NEWLOG[$uid]} ]] || { NEWLOG[$uid]=1; log "$u ($uid): new account, created $REPLY ($(printf %.1f $age) s ago, accountPolicyData), gid $gid" }
    place_wp "$u" "$uid"
    D=$(sudo -u "$u" /usr/bin/getconf DARWIN_USER_DIR 2>/dev/null); C=$(sudo -u "$u" /usr/bin/getconf DARWIN_USER_CACHE_DIR 2>/dev/null)
    [[ $D == /var/folders/*/ && $C == /var/folders/*/ && -d $D && -d $C ]] || { note $uid dirs "$u ($uid): user dirs not ready (D=$D C=$C)"; return 1 }
    if [[ -s "$SEED/ls/$LSN" ]]; then place_ls "$u" "$uid" "$gid" "$D" || return 1
    else LSAT[$uid]=$EPOCHREALTIME; log "$u: no LS store in the payload (BETA7: empty); LS step skipped"; fi
    if [[ -s "$SEED/is/store.index" && -s "$SEED/is/__cache_info__" ]]; then
        I=${C}com.apple.iconservices
        zf_mkdir -p "$I" && zf_chown "$uid:$gid" "$I" && zf_chmod 711 "$I"
        for f in "$SEED"/is/*; do cp -c "$f" "$I/${f:t}" && zf_chown "$uid:$gid" "$I/${f:t}" && zf_chmod 600 "$I/${f:t}"; done
        log "$u: icon index placed"
    else
        log "$u: no icon index in the payload; icon step skipped"
    fi
    place_fonts "$u" $uid $gid "$C"
    UGID[$uid]=$gid; CDIR[$uid]=$C
    print -r "ls placed $LSAT[$uid] created $ct" > "$DONE/$uid.ls"
    pk_arm $uid
    bm_arm $uid "$u"
    return 0
}

ls_pass() {
    local row u uid
    PENDING=0
    for row in ${(f)"$(dscl . -list /Users UniqueID 2>/dev/null)"}; do
        u=${row%% *}; uid=${row##* }
        [[ $uid == <-> ]] && (( uid >= 501 )) || continue
        NAME[$uid]=$u
        [[ -e $DONE/$uid || -e $DONE/$uid.ls || -n ${GAVE[$uid]} ]] && continue
        try_ls "$u" "$uid" || (( ++PENDING ))
    done
    LASTPASS=$EPOCHREALTIME
}

maybe_ls() {   # between Metal chunks: rescan when the users dir changed, or every 1 s while something is pending
    dsig
    if [[ $REPLY != $SIG ]] || (( PENDING && EPOCHREALTIME - LASTPASS >= 1 )); then
        SIG=$REPLY; ls_pass; IDLE_END=$(( EPOCHSECONDS + LINGER ))
    fi
}

# ---- passd kick (a background chain from pk_arm on; the main loop and the Metal chunks only watch it) --------------
# mac-image r7 (2026-11-02): the chain starts as soon as the account is seeded, waits for Apple's passd job in the
# user's gui domain ITSELF (every PKPOLL) and pokes at once. It is a forked subshell running pk_chain (no exec).
# Before r7 the main loop polled gui/<uid> every 2 s between its other launchctl/pgrep forks and only then forked a
# `zsh -fc` chain: bench 103949 (25G332) had gui/501 at 07:48:28.8, the poke logged at 07:48:35.7, `launchctl asuser`
# running at 07:48:50.6 and passd spawned at 07:48:52.3, so its 30 s AppleSSE probe ended 11 s after Setup asked
# passd on leaving the Terms pane (user-seed README, r7).
# states: chain (waiting for gui/<uid>/com.apple.passd, then poking) -> done
pk_arm() {   # $1 uid: after the LS store of a new account is placed
    [[ -x $POKE ]] || { log "${NAME[$1]:-uid$1} ($1): no $POKE; passd not started"; return 0 }
    PKDL[$1]=$(( EPOCHREALTIME + PKWAIT )); PKST[$1]=chain; PKT[$1]=$EPOCHREALTIME; PKNEXT[$1]=0
    pk_bg $1 chain pk_chain $1
}
pk_end() { unset "PKDL[$1]" "PKST[$1]" "PKT[$1]" "PKNEXT[$1]" "PKPID[$1]" "PKOUT[$1]" "PKSEEN[$1]" "PKGUI[$1]" }
pk_chain() {   # $1 uid, in the background: prints "gui <epoch>" once Apple's passd job is in gui/<uid>, then chained
    # pokes (header v5) and "poke n: <passd-poke's line>"; returns 0 when passd answered (anything but a timeout), 3
    # when PKHOLD s of pokes got no answer. pk_pass bounds it (PKWAIT) and kills it with pk_reap.
    local uid=$1 r; local -i n=0; local -F e
    until launchctl print "gui/$uid/$PASSD" >/dev/null 2>&1; do nap $PKPOLL; done
    print -r -- "gui $EPOCHREALTIME"
    e=$(( EPOCHREALTIME + PKHOLD ))
    while (( EPOCHREALTIME < e )); do
        n+=1; r=$(launchctl asuser $uid "$POKE" $POKESVC $PKWAITS 2>&1)
        [[ $r == *timeout* ]] || { print -r -- "poke $n: $r"; return 0 }
    done
    print -r -- "poke $n: $r"; return 3
}
pk_bg() {   # $1 uid, $2 tag, rest = command: run in the background (bounded by pk_reap); output + "rc=N" to a temp file
    local uid=$1 tag=$2 p; shift 2
    PKOUT[$uid]=$DONE/.passd-$uid.$tag.out
    ( "$@"; print -r -- "rc=$?" ) > "${PKOUT[$uid]}" 2>&1 < /dev/null &
    p=$!; PKPID[$uid]=$p       # (zsh: `A[k]=$!` does not expand $!, so via a scalar)
}
pk_killtree() {   # $1 pid: SIGKILL it and all descendants (v5: the poke chain's pokes are grandchildren of pk_bg's subshell)
    local c; for c in $(pgrep -P $1 2>/dev/null); do pk_killtree $c; done; kill -9 $1 2>/dev/null
}
pk_reap() {   # $1 uid: REPLY = "rc N: last output line" of the chain (killed with its children if still running)
    local uid=$1 p=${PKPID[$1]} rc= out=
    local -a L
    if [[ -n $p ]] && kill -0 $p 2>/dev/null; then pk_killtree $p; rc=killed; fi
    [[ -r ${PKOUT[$uid]} ]] && { out=$(<"${PKOUT[$uid]}"); zf_rm -f "${PKOUT[$uid]}" }
    L=("${(@f)out}")
    if [[ ${L[-1]} == rc=* ]]; then [[ -z $rc ]] && rc=${L[-1]#rc=}; L[-1]=(); fi
    unset "PKPID[$uid]"
    REPLY="rc ${rc:-?}${L[-1]:+: ${L[-1]}}"
}
pk_up() { local -a a; a=($(pgrep -u "$1" -x passd 2>/dev/null)); REPLY=${a[1]}; [[ -n $REPLY ]] }
pk_alive() { [[ -n ${PKPID[$1]} ]] && kill -0 ${PKPID[$1]} 2>/dev/null }
pk_pass() {
    local uid u l t p pr r
    for uid in ${(k)PKST}; do
        (( EPOCHREALTIME < PKNEXT[$uid] )) && continue
        PKNEXT[$uid]=$(( EPOCHREALTIME + 0.5 )); u=${NAME[$uid]:-uid$uid}
        if [[ -z ${PKGUI[$uid]} && -r ${PKOUT[$uid]} ]]; then   # has the chain found the job and sent poke 1?
            l=${${(@f)"$(<${PKOUT[$uid]})"}[(r)gui *]}
            if [[ -n $l ]]; then
                PKGUI[$uid]=${l#gui }; hms ${PKGUI[$uid]}; t=$REPLY
                REPLY=$(( (${PKGUI[$uid]} - ${PKT[$uid]}) * 1000 )); REPLY=${REPLY%.*}
                log "$u ($uid): gui/$uid/$PASSD there at $t (+$REPLY ms after the LS store); XPC poke $POKESVC via launchctl asuser $uid"
                PKT[$uid]=${PKGUI[$uid]}
            fi
        fi
        if [[ -n ${PKGUI[$uid]} ]] && pk_up $uid; then
            p=$REPLY
            if [[ -z ${PKSEEN[$uid]} ]]; then
                PKSEEN[$uid]=$p; pr=$(ps -o pri= -p $p 2>/dev/null); ms ${PKT[$uid]}
                log "$u ($uid): passd $p seen +$REPLY ms after the first poke (priority ${pr// /}; launchd logs the spawn); keeping the poke chain pending until it answers"
            fi
            pk_alive $uid && continue
            pk_reap $uid; r=$REPLY; pr=$(ps -o pri= -p $p 2>/dev/null); ms ${PKT[$uid]}
            if [[ $r == *timeout* || $r == *killed* ]]; then
                log "$u ($uid): passd $p did not answer within ${PKHOLD} s of chained pokes (+$REPLY ms; last: $r; priority now ${pr// /})"
            else
                log "$u ($uid): passd $p answered the poke chain (+$REPLY ms after the first poke; $r; priority now ${pr// /})"
            fi
            pk_end $uid; continue
        fi
        if (( EPOCHREALTIME >= PKDL[$uid] )); then
            pk_reap $uid
            log "$u ($uid): passd kick gave up after ${PKWAIT} s (${PKGUI[$uid]:+poked; }last: $REPLY); passd not running"
            pk_end $uid; continue
        fi
        pk_alive $uid && continue
        pk_reap $uid; log "$u ($uid): poke chain ended without a passd process ($REPLY); stop watching"; pk_end $uid
    done
}

# ---- phase 2: Metal trees for every account whose LS store is placed but that has no final marker ------------------
place_chunk() {   # $1 u $2 uid $3 gid $4 C $5 metal start, rest = tree names. Sets NB NS (placed / kept counters)
    local u=$1 uid=$2 gid=$3 C=$4 t0=$5 n S; shift 5
    local -a todo src
    for n in "$@"; do
        if [[ -e $C$n ]]; then (( ++NS )); KEPT+=($n); else todo+=($n); src+=("$SEED/metal/$n"); fi
    done
    (( ${#todo} )) || return 0
    S=${C}.peruser-seed.stage; zf_rm -rf "$S"; zf_mkdir -p "$S" || { log "$u: FAILED to create $S"; return 1 }
    if ! cp -Rpc "${src[@]}" "$S/" || ! own "$uid:$gid" "$S"; then
        log "$u: FAILED to copy metal chunk (${todo[*]})"; zf_rm -rf "$S"; return 1
    fi
    local -a i0 i1 i2
    for n in $todo; do
        if [[ -e $C$n ]]; then (( ++NS )); KEPT+=($n); continue; fi     # appeared during the copy: keep the app's
        i0=(); zstat -A i0 +inode "$S/$n" 2>/dev/null || { log "$u: FAILED: metal $n missing from the stage"; continue }
        if zf_mv "$S/$n" "$C$n"; then
            # Trees may legitimately hold a subdir named like the bundle (com.apple.Safari/com.apple.Safari/...), so
            # "did ours land?" is decided by inode: if $C$n appeared between the check and the rename, the rename
            # put ours INSIDE theirs as $C$n/$n; remove only that (same inode), keep theirs.
            i1=(); zstat -A i1 +inode "$C$n" 2>/dev/null
            if [[ ${i1[1]} == ${i0[1]} ]]; then (( ++NB )); ms $t0; log "$u: metal $n placed +$REPLY ms"
            else
                i2=(); zstat -A i2 +inode "$C$n/$n" 2>/dev/null
                [[ -n ${i2[1]} && ${i2[1]} == ${i0[1]} ]] && zf_rm -rf "$C$n/$n"
                (( ++NS )); KEPT+=($n); log "$u: metal $n appeared during the rename: kept the app's tree"
            fi
        else log "$u: FAILED to rename metal $n"; fi
    done
    zf_rm -rf "$S"
}
metal_pass() {
    local m uid u gid C t0 n i
    local -a all order first
    for m in $DONE/<->.ls; do
        uid=${${m:t}%.ls}; [[ -e $DONE/$uid ]] && { zf_rm -f $m; continue }
        [[ -n ${MFAIL[$uid]} ]] && continue
        u=${NAME[$uid]}; [[ -n $u ]] || { MFAIL[$uid]=1; log "uid $uid: has an LS marker but no account; metal skipped"; continue }
        gid=${UGID[$uid]:-$(id -g "$u" 2>/dev/null)}; C=${CDIR[$uid]:-$(sudo -u "$u" /usr/bin/getconf DARWIN_USER_CACHE_DIR 2>/dev/null)}
        [[ $gid == <-> && $C == /var/folders/*/ && -d $C ]] || { MFAIL[$uid]=1; log "$u ($uid): metal: cannot resolve gid/cache dir (gid=$gid C=$C); skipped this run"; continue }
        all=("$SEED"/metal/*(/:t)); order=()
        for n in $FIRSTUSE; do (( ${all[(Ie)$n]} )) && order+=($n); done
        for n in $all; do (( ${order[(Ie)$n]} )) || order+=($n); done
        NB=0 NS=0 KEPT=(); t0=$EPOCHREALTIME
        first=(${order:*FIRSTUSE}); order=(${order:|FIRSTUSE})
        log "$u: metal: $(( ${#first} + ${#order} )) trees, first-use group ${#first} (${first[*]})"
        place_chunk "$u" $uid $gid "$C" $t0 $first
        for (( i = 1; i <= ${#order}; i += CHUNK )); do
            maybe_ls; pk_pass; bm_pass; ef_pass
            place_chunk "$u" $uid $gid "$C" $t0 ${order[i,i+CHUNK-1]}
        done
        ms $t0
        log "$u: metal: $NB bundle trees placed, $NS already present (kept${KEPT:+: ${KEPT[*]}}) in $REPLY ms"
        print -r "seeded $(<$m)" > "$DONE/$uid" && zf_rm -f $m
        log "$u ($uid): seeded, C=$C"
        IDLE_END=$(( EPOCHSECONDS + LINGER ))
    done
}

dsig; SIG=$REPLY
hms ${SIG%%:*}; log "run start (users dir changed $REPLY)"
IDLE_END=$(( EPOCHSECONDS + LINGER )); HARD_END=$(( EPOCHSECONDS + MAXRUN ))
ls_pass
while :; do
    metal_pass
    pk_pass
    bm_pass
    ef_pass
    dsig
    if [[ $REPLY != $SIG ]]; then SIG=$REPLY; IDLE_END=$(( EPOCHSECONDS + LINGER )); ls_pass; continue; fi
    (( PENDING )) && ls_pass
    (( EPOCHSECONDS >= HARD_END )) && break
    metal_todo; (( PENDING == 0 && REPLY == 0 && ${#PKST} == 0 && ${#BMST} == 0 && EPOCHSECONDS >= IDLE_END )) &&
        [[ $EFST != wait || ${#NAME} == 0 ]] && break
    nap 25
done
(( PENDING )) && log "gave up on $PENDING account(s) at the ${MAXRUN} s cap; the next users-dir change retries"
for uid in ${(k)PKST}; do [[ -n ${PKPID[$uid]} ]] && pk_reap $uid; log "${NAME[$uid]:-uid$uid} ($uid): passd kick unfinished at the ${MAXRUN} s cap (state ${PKST[$uid]})"; done
for uid in ${(k)BMST}; do log "${NAME[$uid]:-uid$uid} ($uid): aerial still unfinished at the ${MAXRUN} s cap (${BMWHY[$uid]})"; done
ef_final "run end"
log "run end"
exit 0
# optional self-retire after the first real account, to return to a stock system (not enabled for the test):
# [[ -n $(ls $DONE) ]] && { rm -f /Library/LaunchDaemons/local.peruser-seed.plist; rm -rf "$SEED"; launchctl bootout system/local.peruser-seed }
