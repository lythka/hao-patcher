#!/bin/bash
# Workflow plan step 15: the end-user installer's riskiest unknowns, tried in the laptop's NetBoot Recovery (Tahoe
# 25G241, x86_64, root SSH) against image files on RAM disks. Run by the holder of the laptop lock while the laptop
# is in that Recovery; nothing here reboots it, and the internal disk is only ever read.
#
#   tools/recovery-spikes/run.sh survey     read-only facts: build, memory, tools, disks (no RAM disk) ~1 min
#   tools/recovery-spikes/run.sh python     (a) the bundled x86_64 Python: modules, ctypes, APFS.framework, suites ~3 min
#   tools/recovery-spikes/run.sh cryptex    (c) rebuild + seal the App cryptex with install.py's own steps ~5 min
#   tools/recovery-spikes/run.sh group      (d) APFSContainerVolumeGroupAdd vs -groupWith, and the fl entitlement ~3 min
#   tools/recovery-spikes/run.sh kmutil     (b) link Boot/System KCs as darwin27-kernel does, then kc-binder ~10 min
#   tools/recovery-spikes/run.sh all        the five in that order (stops after python if python fails)
#   tools/recovery-spikes/run.sh clean      undo every spike's RAM disks, images and mounts on the laptop
#
# Each spike: verify its inputs (inputs.lock sha256) and stage them on the host; gate (refuses unless the laptop
# runs the Recovery RECOVERY_BUILD as root); a RAM disk at /Volumes/HAOSPIKE-SPIKE; copy the inputs there
# (laptop-ssh --put; the laptop re-checks every sha256); run; copy RESULTS back; clean (also on failure or ^C);
# verdict PASS / PARTIAL / FAIL (lib/spikes.py; README.md says what each means).
#
# Environment: LAPTOP_SSH (default $HOME/hao-tools/tools/laptop-ssh, with its LAPTOP=root@ADDR and LAPTOP_KEY; on the
# home LAN set LAPTOP=root@192.168.68.58), RECOVERY_BUILD (default 25G241), RESULTS (default
# REPO/store/runs/recovery-spikes-YYYYMMDD), FILL_MB (cryptex filler, default 256), KC_SCOPE (kmutil: auto|full|boot,
# default auto), HOST_PYTHON (default python3.14), LOCK (default $HOME/mac-image/store/tmp/laptop.lock).
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
repo=$(cd "$here/../.." && pwd)
ssh_tool=${LAPTOP_SSH:-$HOME/hao-tools/tools/laptop-ssh}
py=${HOST_PYTHON:-python3.14}
build=${RECOVERY_BUILD:-25G241}
results=${RESULTS:-$repo/store/runs/recovery-spikes-$(date +%Y%m%d)}
fill=${FILL_MB:-256}
scope=${KC_SCOPE:-auto}
lock=${LOCK:-$HOME/mac-image/store/tmp/laptop.lock}
W=/private/var/tmp/hao-spikes            # on the laptop: the remote scripts and state files
lib() { "$py" -I "$here/lib/spikes.py" "$@"; }
die() { echo "recovery-spikes: $*" >&2; exit 1; }
raw=()
on() { local t=$1; shift; "$ssh_tool" ${raw[@]+"${raw[@]}"} -t "$t" -- "$@"; }
put() { "$ssh_tool" ${raw[@]+"${raw[@]}"} --put "$1" "$2"; }

connect() {   # once per run: perl for laptop-ssh's remote alarm? (-r without it), the gate, the scripts
    local perl
    perl=$("$ssh_tool" -r -t 30 -- 'command -v perl >/dev/null 2>&1 && echo yes || echo no') ||
        die "no SSH to the laptop (LAPTOP=${LAPTOP:-default of $ssh_tool})"
    [ "$perl" = yes ] || { raw=(-r); echo "note: no perl in the Recovery; laptop-ssh -r (local timeouts only)"; }
    on 60 "mkdir -p $W && chmod 700 $W"
    for f in "$here"/remote/*.sh; do put "$f" "$W/$(basename "$f")"; done
    on 60 "sh $W/common.sh gate $build" || die "gate refused: the laptop is not in the NetBoot Recovery $build"
}

spike() {   # spike NAME: one spike end to end; prints its verdict; returns 1 on FAIL
    local name=$1 out stage T mb rc=0 args=()
    out=$results/$name-$(date +%H%M%S); mkdir -p "$out"
    stage=$(mktemp -d "${TMPDIR:-/tmp}/recovery-spikes.XXXXXX")
    echo "== $name -> $out"
    lib stage "$name" "$stage" > "$stage/files.txt"
    if [ "$name" = survey ]; then
        on "$(lib timeout survey)" "sh $W/survey.sh" > "$out/log.txt" || true
    else
        T=/Volumes/HAOSPIKE-$name
        mb=$(lib ramdisk "$name" "$fill")
        # clean even when the run fails or is interrupted
        trap "on 300 'sh $W/common.sh clean $name' >> '$out/clean.txt' 2>&1 || true; rm -rf '$stage'" EXIT
        on 120 "sh $W/common.sh setup $name $mb" | tee "$out/setup.txt"
        grep -q '^SETUP OK' "$out/setup.txt" || { echo "setup failed"; return 1; }
        while read -r f; do put "$stage/$f" "$T/$f"; done < "$stage/files.txt"
        case $name in cryptex) args=("$fill") ;; kmutil) args=("$scope") ;; esac
        on "$(lib timeout "$name")" "sh $W/spike.sh $T $name ${args[*]+${args[*]}}" | tee "$out/log.txt" || true
        on 900 "cd $T && tar -cf - results" > "$out/results.tar" && tar -xf "$out/results.tar" -C "$out" ||
            echo "warning: could not copy $T/results back"
        on 300 "sh $W/common.sh clean $name" | tee "$out/clean.txt" || true
        trap - EXIT; rm -rf "$stage"
    fi
    lib verdict "$name" "$out/log.txt" --results "$out/results" --out "$out/verdict.json" | tee "$out/verdict.txt" || rc=1
    return $rc
}

step=${1:-}
[ -n "$step" ] || { sed -n '2,/^set -euo/p' "$0" | sed '$d; s/^# \{0,1\}//'; exit 2; }
[ -e "$lock" ] || echo "recovery-spikes: note: $lock does not exist; laptop runs belong to the lock holder" >&2
case $step in
survey|python|cryptex|group|kmutil)
    mkdir -p "$results"; connect; spike "$step" ;;
all)
    mkdir -p "$results"; connect; bad=0
    spike survey || bad=1
    spike python || { echo "python failed: the other spikes need it"; exit 1; }
    for s in cryptex group kmutil; do spike "$s" || bad=1; done
    exit $bad ;;
clean)
    connect
    for s in python cryptex group kmutil; do on 300 "sh $W/common.sh clean $s" || true; done
    on 60 "rm -rf $W" ;;
*) die "unknown step $step (survey, python, cryptex, group, kmutil, all, clean)" ;;
esac
