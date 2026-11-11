#!/bin/sh
# cache/seeds/collect.sh: read-only harvest of the caches a running image made, for cache/seeds/make.
# Run on the laptop as root, output on stdout:
#   ssh root@LAPTOP 'sh -s' < cache/seeds/collect.sh > store/artifacts/harvest-<build>-<image>-<stamp>.tar
# The tar holds, with paths relative to / (= Data-rooted: private/var/folders, Library/Caches):
#   every account's Metal caches   private/var/folders/*/*/C/**/com.apple.metal{,fe}/
#   the shared icon store          Library/Caches/com.apple.iconservices.store/
#   every account's icon index     private/var/folders/*/*/C/com.apple.iconservices/
#   every account's font registry  private/var/folders/*/*/C/com.apple.FontRegistry/ (with -wal/-shm as they are)
#   diag/                          what the boot-2 storm hypotheses need (plan.md section 3), written to a temp folder
# Nothing on the laptop is changed except that temp folder, which is removed at exit.
set -u
export COPYFILE_DISABLE=1 LC_ALL=C
T=$(mktemp -d /private/var/tmp/seedharvest.XXXXXX) || exit 1
trap 'rm -rf "$T"' EXIT
D=$T/diag
mkdir -p "$D"
R=${SEED_ROOT:-/}   # tests point this at a fixture tree
cd "$R" || exit 1

# Paths to archive (relative to /), one per line.
find private/var/folders -maxdepth 8 -type d \( -name com.apple.metal -o -name com.apple.metalfe \) -prune -print \
    2>/dev/null > "$T/metal.txt"
find private/var/folders -maxdepth 4 -type d \( -name com.apple.iconservices -o -name com.apple.FontRegistry \) \
    -path '*/C/*' -prune -print 2>/dev/null > "$T/other.txt"
[ -d Library/Caches/com.apple.iconservices.store ] && echo Library/Caches/com.apple.iconservices.store >> "$T/other.txt"
cat "$T/metal.txt" "$T/other.txt" > "$T/paths.txt"

{
    sw_vers; echo; sysctl -n kern.boottime; date -u; uptime
    echo; echo "## last reboots"; last reboot 2>/dev/null | head -10
    echo; echo "## accounts"; dscl . -list /Users UniqueID | awk '$2 >= 500 || $2 == 0 || $2 == 88 || $2 == 248'
} > "$D/system.txt" 2>&1

# Per account cache folder: owner uid, path (DARWIN_USER_CACHE_DIR = <folder>/C).
for c in private/var/folders/*/*/C; do
    [ -d "$c" ] && printf '%s\t%s\n' "$(stat -f %u "${c%/C}")" "$c"
done > "$D/cache-dirs.tsv" 2>/dev/null

# H2: Metal trees: every file with size and mtime (written on boot 1? grown on boot 2?).
while IFS= read -r p; do
    find "$p" -exec stat -f '%Su:%Sg %Lp %z %Sm %N' -t '%Y-%m-%dT%H:%M:%S' {} + 2>/dev/null
done < "$T/metal.txt" > "$D/metal-files.txt"
# H3: icon store and indexes.
{
    echo "## store"; ls -ld Library/Caches/com.apple.iconservices.store 2>&1
    echo "units: $(ls Library/Caches/com.apple.iconservices.store 2>/dev/null | grep -c isdata)"
    ls -lT Library/Caches/com.apple.iconservices.store/store-info.plist 2>&1
    for i in private/var/folders/*/*/C/com.apple.iconservices; do
        [ -d "$i" ] || continue
        echo "## $i"; ls -laT "$i"; plutil -p "$i/__cache_info__" 2>&1
        [ -f "$i/store.index" ] && xxd -l 20 "$i/store.index"
    done
} > "$D/icons.txt" 2>&1
# H1: hao's translation cache.
{ ls -laR /private/var/db/hao 2>&1 | head -400; du -sk /private/var/db/hao 2>&1; } > "$D/hao.txt"
# H4: who used the CPU this boot.
ps -axo pid,ppid,user,time,%cpu,lstart,comm -r > "$D/ps.txt" 2>&1
ls -lT /Library/Logs/DiagnosticReports /private/var/db/DiagnosticReports 2>/dev/null > "$D/diagnostic-reports.txt"
# Folder metadata the extraction must reproduce (owners, modes, flags, xattrs) for every archived root's ancestors.
while IFS= read -r p; do
    q=$p
    while [ "$q" != private/var/folders ] && [ "$q" != Library/Caches ] && [ "$q" != . ]; do
        stat -f '%Lp %u %g %Sf %N' "$q"; q=$(dirname "$q")
    done
done < "$T/paths.txt" 2>/dev/null | sort -u -k5 > "$D/folders.txt"
while IFS= read -r p; do xattr -lr "$p" 2>/dev/null | head -200; done < "$T/paths.txt" > "$D/xattrs.txt"
# Counts of this boot's Metal compiles and icon renders (cheap predicates; the full archive is not collected).
[ -n "${SEED_NO_LOG:-}" ] || {
    echo "MTLCompilerService lines: $(log show --last boot --style compact --predicate 'process == "MTLCompilerService"' 2>/dev/null | wc -l)"
    echo "Generated image (iconservices): $(log show --last boot --style compact --predicate 'subsystem == "com.apple.iconservices"' 2>/dev/null | grep -c 'Generated image')"
    echo "Rejected cached image: $(log show --last boot --style compact --predicate 'subsystem == "com.apple.iconservices"' 2>/dev/null | grep -c 'Rejected cached image')"
} > "$D/log-counts.txt" 2>&1

tar -cf "$T/diag.tar" --numeric-owner -C "$T" diag 2>/dev/null
cd "$R" || exit 1
# One stream: the cache trees, then the diag tar's members (tar concatenation by bsdtar @archive).
tar -cf - --numeric-owner --no-mac-metadata -T "$T/paths.txt" @"$T/diag.tar" 2>"$T/tar.err"
r=$?
[ $r -eq 0 ] || { echo "collect.sh: tar exit $r: $(tail -5 "$T/tar.err")" >&2; }
exit 0
