#!/bin/sh
# cache/seeds/collect-template.sh: read-only harvest of what macOS made in one account's Library at its first login,
# for cache/seeds/template (the User Template, patches/golden-gate-user-template). Run on the laptop as root:
#   ssh root@LAPTOP 'SEED_USER=benchtest sh -s' < cache/seeds/collect-template.sh \
#       > store/artifacts/template-harvest-<build>-<image>-<stamp>.cpio
# Output on stdout: a ditto (cpio) archive, because tar's headers cannot carry the containermanager xattrs (the
# data-container personality is ~0.1-2 MB). Paths in it:
#   Library/User Template/Non_localized/Library/Containers/<id>/          every container folder with its xattrs,
#       .com.apple.containermanagerd.metadata.plist and Data/SystemData/com.apple.chrono/placeholders/** only
#   .../Library/Group Containers/group.com.apple.chronod/                 metadata plist, chronod/chrono.sql
#   .../Library/Group Containers/group.com.apple.reminders/               metadata plist, Container_v1/Stores/*.sqlite
#   .../Library/Preferences/com.apple.chronod.plist
#   diag/system.txt (sw_vers), diag/account.txt (user, uid, home, real name, creation time), diag/containers.tsv
#       (birth time, xattr names, folder of every container), diag/symlinks.txt (link count per root)
# No app data leaves the laptop: container contents other than the metadata and widget placeholders are not copied.
# SQLite databases are copied with sqlite3 .backup (a consistent copy while chronod/remindd hold them open).
# Nothing on the laptop is changed except a temp folder under /private/var/tmp, removed at exit.
# Tests: SEED_HOME points at a fixture home (no dscl).
set -u
export COPYFILE_DISABLE=1 LC_ALL=C
U=${SEED_USER:?set SEED_USER to the account whose first login is harvested}
if [ -n "${SEED_HOME:-}" ]; then
    H=$SEED_HOME; REAL=${SEED_REALNAME:-}; CREATED=
else
    H=$(dscl . -read "/Users/$U" NFSHomeDirectory 2>/dev/null | awk '{print $2}')
    REAL=$(dscl -plist . -read "/Users/$U" RealName 2>/dev/null | plutil -extract dsAttrTypeStandard:RealName.0 raw - 2>/dev/null)
    CREATED=$(dscl -plist . -read "/Users/$U" accountPolicyData 2>/dev/null | plutil -extract dsAttrTypeNative:accountPolicyData.0 raw - 2>/dev/null \
              | plutil -extract creationTime raw - 2>/dev/null)
fi
[ -n "$H" ] && [ -d "$H/Library" ] || { echo "collect-template.sh: no home for $U" >&2; exit 1; }
T=$(mktemp -d /private/var/tmp/seedtemplate.XXXXXX) || exit 1
trap 'rm -rf "$T"' EXIT
S=$T/stage
L="$S/Library/User Template/Non_localized/Library"
D=$S/diag
mkdir -p "$L/Containers" "$L/Group Containers" "$L/Preferences" "$D"
SRC=$H/Library

# Copy each root whole (ditto keeps modes and xattrs; the roots are small: ~30 MB on 26B131), then drop every
# file the template does not take, so app data stays on the laptop. Databases are replaced by sqlite3 .backup copies.
for r in Containers "Group Containers/group.com.apple.chronod" "Group Containers/group.com.apple.reminders"; do
    [ -d "$SRC/$r" ] && ditto "$SRC/$r" "$L/$r"
done
[ -f "$SRC/Preferences/com.apple.chronod.plist" ] && ditto "$SRC/Preferences/com.apple.chronod.plist" "$L/Preferences/com.apple.chronod.plist"
for c in "$SRC/Containers"/*; do
    [ -d "$c" ] && printf '%s\t%s\t%s\n' "$(stat -f %B "$c")" "$(xattr "$c" 2>/dev/null | tr '\n' ',')" "${c##*/}"
done > "$D/containers.tsv"
find "$L/Containers" "$L/Group Containers" -type f ! -name .com.apple.containermanagerd.metadata.plist \
    ! -path '*/Data/SystemData/com.apple.chrono/placeholders/*' -delete
backup() {   # $1 database, $2 target: a consistent copy, WAL folded in
    mkdir -p "$(dirname "$2")"
    /usr/bin/sqlite3 -readonly "$1" ".backup '$2'" 2>>"$D/sqlite.err" || echo "backup failed: $1" >> "$D/sqlite.err"
}
G="$SRC/Group Containers/group.com.apple.chronod/chronod/chrono.sql"
[ -f "$G" ] && backup "$G" "$L/Group Containers/group.com.apple.chronod/chronod/chrono.sql"
for s in "$SRC/Group Containers/group.com.apple.reminders/Container_v1/Stores"/*.sqlite; do
    [ -f "$s" ] && backup "$s" "$L/Group Containers/group.com.apple.reminders/Container_v1/Stores/${s##*/}"
done

{ sw_vers; echo; date -u; uptime; } > "$D/system.txt" 2>&1
printf 'user\t%s\nuid\t%s\nhome\t%s\nrealname\t%s\ncreated\t%s\n' "$U" "$(stat -f %u "$H")" "$H" "$REAL" "$CREATED" > "$D/account.txt"
for r in Containers "Group Containers"; do
    printf '%s\t%s\n' "$r" "$(find "$SRC/$r" -type l 2>/dev/null | wc -l | tr -d ' ')"
done > "$D/symlinks.txt"

ditto -c "$S" - 2>"$T/ditto.err"
r=$?
[ $r -eq 0 ] || echo "collect-template.sh: ditto exit $r: $(tail -5 "$T/ditto.err")" >&2
exit $r
