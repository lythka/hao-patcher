#!/bin/sh
# CB1 crash-free boot (owner 2026-11-18: "We should really have a crash free boot whether or not it's cache optimized").
# Run as root on the laptop when the first login has settled (load1 < 4). From boot until now it lists:
#   - every crash report (DiagnosticReports of the system and of every user: .ips .crash .panic .diag; JetsamEvent counts),
#     with the process and the exception or termination reason;
#   - every launchd job with more than 5 abnormal exits (non-zero exit, a signal, a jetsam kill; not launchd's own teardown or
#     shutdown kills) within any 5 minutes (pm 2026-11-18), with its last exit reason and its raw spawn count.
# PASS only when both lists are empty. Output: "HAOCHECK CHECK crash-free-boot PASS|FAIL <counts>", one
# "HAOCHECK CB1 <process> <count> <crash|respawn> <reason>" line per entry, then "HAOCHECK END crash-free-boot".
# OUT (optional): where cb1-launchd.log and cb1-list.tsv go. Changes nothing.
PATH=/usr/bin:/bin:/usr/sbin:/sbin; export PATH; LC_ALL=C; export LC_ALL
OUT=${OUT:-/private/var/tmp/hao-check/out}; mkdir -p "$OUT"
BT=$(sysctl -n kern.boottime | sed -E 's/^\{ sec = ([0-9]+),.*/\1/')
START=$(date -r "$BT" "+%Y-%m-%d %H:%M:%S")
: > "$OUT/cb1-list.tsv"
# crash reports since boot
for d in /Library/Logs/DiagnosticReports /Library/Logs/DiagnosticReports/Retired /Users/*/Library/Logs/DiagnosticReports; do
  [ -d "$d" ] || continue
  find "$d" -maxdepth 1 -type f \( -name '*.ips' -o -name '*.crash' -o -name '*.panic' -o -name '*.diag' \) -newermt "$START" 2>/dev/null
done | while read -r f; do
  b=$(basename "$f"); p=$(echo "$b" | sed -E 's/[-_]20[0-9]{2}-[0-9]{2}-[0-9]{2}.*//')
  r=$(sed -n '2,$p' "$f" 2>/dev/null | grep -o -E '"(type|signal|indicator)" ?: ?"[^"]*"' | head -3 | sed -E 's/.*: ?"//; s/"$//' | tr '\n' ' ')
  case $b in *cpu_resource.diag|*wakeups_resource.diag|*diskwrites_resource.diag) continue;; *.diag) r="diag";; esac
  printf '%s\tcrash\t%s\n' "$p" "${r:-?}"
done | sort | awk -F'\t' '{k=$1"\t"$2; n[k]++; r[k]=$3} END{for(k in n) {split(k, a, "\t"); printf "%s\t%d\t%s\t%s\n", a[1], n[k], a[2], r[k]}}' >> "$OUT/cb1-list.tsv"
# launchd respawns since boot
log show --start "$START" --predicate 'process == "launchd" AND (eventMessage CONTAINS "xpcproxy spawned with pid" OR eventMessage CONTAINS "exited")' --style compact 2>/dev/null > "$OUT/cb1-launchd.log"
awk '
  { if (match($0, /\[(system|gui\/[0-9]+|user\/[0-9]+|pid\/[0-9]+)\/[^ \]]+/)) { lab=substr($0, RSTART+1, RLENGTH-1); sub(/^[^\/]+\/([0-9]+\/)?/, "", lab) } else next
    if (lab ~ /^dev\.hao\./) next   # test-machine tooling (GUI helper, diagnosis windows), not the image
    split($2, t, ":"); s=t[1]*3600+t[2]*60+t[3]
    if ($0 ~ /xpcproxy spawned with pid/) { sp[lab]++; next }
    if ($0 !~ /\] exited (with|due)/) next
    m=$0; sub(/.*\] exited /, "exited ", m)
    # pm 2026-11-18: only abnormal ends count (non-zero exit, a signal, a jetsam kill); launchd teardown or shutdown kills do not
    if (m ~ /due to exit\(0\)/) next
    if (m ~ /sent by launchd\[1\] during (teardown|system shutdown)/) next
    if (m !~ /due to exit\(|due to SIG|JETSAM|exit reason/) next
    c[lab]++; ts[lab, c[lab]]=s; why[lab]=m }
  END { for (l in c) { best=0; j=1
          for (i=1; i<=c[l]; i++) { while (ts[l,i]-ts[l,j] > 300) j++; if (i-j+1 > best) best=i-j+1 }
          if (best > 5) printf "%s\trespawn\t%d\t%d abnormal exits in 5 min (%d abnormal, %d spawns since boot); %s\n", l, c[l], best, c[l], sp[l], why[l] } }' "$OUT/cb1-launchd.log" |
  awk -F'\t' '{printf "%s\t%s\t%s\t%s\n", $1, $3, $2, $4}' >> "$OUT/cb1-list.tsv"
n=$(wc -l < "$OUT/cb1-list.tsv" | tr -d ' ')
cr=$(awk -F'\t' '$3=="crash"' "$OUT/cb1-list.tsv" 2>/dev/null | wc -l | tr -d ' ')
awk -F'\t' '{printf "HAOCHECK CB1 %s %s %s %s\n", $1, $2, $3, $4}' "$OUT/cb1-list.tsv"
if [ "$n" = 0 ]; then echo "HAOCHECK CHECK crash-free-boot PASS no crash report and no job over 5 spawns in 5 min since boot ($START)"
else echo "HAOCHECK CHECK crash-free-boot FAIL $n entries ($cr crash, $((n-cr)) respawn) since boot ($START); list in $OUT/cb1-list.tsv"; fi
echo "HAOCHECK END crash-free-boot"
