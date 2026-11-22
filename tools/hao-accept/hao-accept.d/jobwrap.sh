#!/bin/sh
# hao-accept job, guest part 2: the TEST-ONLY launchd job (dev.hao.job) on the installed system's boot. Runs job.sh with
# OUT (a folder for its trace files) and PAYLOAD, copies OUT to the HAOACCEPT test disk, removes itself, powers off.
# A 60 min watchdog powers off whatever happens.
# GC.md "Store integrity": no .pyc beside a USB installer's or the store's Python files.
export PYTHONDONTWRITEBYTECODE=1
H=/Library/hao-accept; OUT=$H/out; mkdir -p "$OUT"
say() { echo "HAOACCEPT $*" | tee -a "$OUT/job.log" > /dev/console; }
( sleep 3600; say "WATCHDOG power-off after 60 min"; sync; shutdown -h now ) &
boot=$(sysctl -n kern.boottime | sed -n 's/.*sec = \([0-9]*\),.*/\1/p')
say "BEGIN job $(date -u +%s) uptime $(( $(date -u +%s) - boot ))"
# Harness setting: no sleep for the whole job (display, idle, disk, system), so a sleep/wake fault (diag #79, a
# USB EHCI wake panic) cannot fail an acceptance run; sleep/wake is tested on its own.
caffeinate -dims -w $$ > /dev/null 2>&1 &
say "INFO caffeinate -dims for the job (harness setting)"
OUT=$OUT PAYLOAD=$H/payload sh "$H/job.sh" > "$OUT/job-stdout.txt" 2>&1
say "JOB rc $?"
rm -f /Library/LaunchDaemons/dev.hao.job.plist
# The results go to the console (serial) first: in the hao verify job (2026-11-17) the diskutil step below never
# returned, and the copy to the test disk never happened. Each file, up to 400 lines, as "OUT|name| line".
for f in "$OUT"/*; do [ -f "$f" ] && head -400 "$f" | sed "s|^|HAOACCEPT OUT\|$(basename "$f")\| |" > /dev/console; done
say "END job $(date -u +%s)"
sync
( sleep 180; sync; shutdown -h now ) &
v=$(diskutil list | awk '/HAOACCEPT/{print $NF; exit}')
[ -n "$v" ] && diskutil mount "$v" > /dev/null 2>&1
m=$(diskutil info "$v" 2>/dev/null | sed -n 's/^ *Mount Point: *//p')
if [ -n "$m" ] && [ -d "$m" ]; then mkdir -p "$m/out"; cp -R "$OUT/" "$m/out/"; sync; diskutil unmount "$m" > /dev/null 2>&1; fi
sync
shutdown -h now
