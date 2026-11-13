#!/bin/sh
# Runs ON THE LAPTOP in the NetBoot Recovery, as root, from /private/var/tmp/hao-spikes (run.sh copies it there).
#   sh common.sh gate BUILD          refuse (exit 3) unless this is the x86_64 NetBoot Recovery of BUILD, as root
#   sh common.sh mem                 memory a RAM disk may use, in MB (free + inactive + speculative + purgeable pages)
#   sh common.sh setup SPIKE MB      RAM disk of MB MB, HFS+ with owners, at /Volumes/HAOSPIKE-SPIKE (inputs/ results/)
#   sh common.sh clean SPIKE         undo what SPIKE made: every line of its state file in reverse, then any image
#                                    still attached from its RAM disk, then the RAM disk; safe to repeat
# Scratch is RAM only: RAM disks (hdiutil attach -nomount ram://) and image files on them. The internal disk is
# never written; kmutil's spike mounts its System volume read-only and records that mount here so clean undoes it.
# State file W/SPIKE.state, one action per line, appended before the action is taken:
#   ramdisk /dev/diskN | image /dev/diskN | mount /path
set -u
W=/private/var/tmp/hao-spikes
cmd=${1:-}; [ $# -gt 0 ] && shift

mem_mb() {
    # The NetBoot Recovery (25G241) has no vm_stat: use the kernel's own page counts (free + speculative +
    # purgeable; inactive is not exported, so this errs low).
    if ! command -v vm_stat >/dev/null 2>&1; then
        sysctl -n vm.pagesize vm.page_free_count vm.page_speculative_count vm.page_purgeable_count 2>/dev/null |
            awk 'NR == 1 { ps = $1; next } { n += $1 } END { printf "%d\n", n * ps / 1048576 }'
        return
    fi
    vm_stat | awk '/page size of/ { for (i = 1; i <= NF; i++) if ($i == "of") ps = $(i + 1) }
        /^Pages (free|inactive|speculative|purgeable):/ { gsub(/\./, "", $NF); n += $NF }
        END { if (!ps) ps = 4096; printf "%d\n", n * ps / 1048576 }'
}

case $cmd in
gate)
    want=${1:?BUILD}
    build=$(sw_vers -buildVersion 2>/dev/null); arch=$(uname -m); uid=$(id -u)
    echo "GATE build $build arch $arch uid $uid"
    [ "$build" = "$want" ] || { echo "GATE FAIL not the Recovery $want (running $build): boot the NetBoot Recovery first"; exit 3; }
    [ "$arch" = x86_64 ] || { echo "GATE FAIL not x86_64"; exit 3; }
    [ "$uid" = 0 ] || { echo "GATE FAIL not root"; exit 3; }
    [ -x /usr/local/ssdtools/bin/ssdpush-agent ] || { echo "GATE FAIL no /usr/local/ssdtools: not the NetBoot Recovery"; exit 3; }
    echo "GATE OK" ;;
mem)
    mem_mb ;;
setup)
    spike=${1:?SPIKE}; mb=${2:?MB}; name=HAOSPIKE-$spike; T=/Volumes/$name
    mkdir -p "$W"; chmod 700 "$W"
    [ -e "$T" ] && { echo "SETUP FAIL $T exists: run.sh clean first"; exit 1; }
    [ -s "$W/$spike.state" ] && { echo "SETUP FAIL $W/$spike.state is left from an earlier run: run.sh clean first"; exit 1; }
    avail=$(mem_mb)
    echo "SETUP memory available ${avail} MB, RAM disk ${mb} MB"
    [ "$avail" -ge $((mb + 1024)) ] || { echo "SETUP FAIL not enough memory for a ${mb} MB RAM disk (+1024 MB margin)"; exit 1; }
    dev=$(hdiutil attach -nomount "ram://$((mb * 2048))" | awk 'NR == 1 {print $1}')
    case $dev in /dev/disk[0-9]*) ;; *) echo "SETUP FAIL hdiutil ram:// gave '$dev'"; exit 1 ;; esac
    echo "ramdisk $dev" >> "$W/$spike.state"
    diskutil erasevolume HFS+ "$name" "$dev" >/dev/null || { echo "SETUP FAIL erasevolume $dev"; exit 1; }
    [ -d "$T" ] || { echo "SETUP FAIL $T not mounted"; exit 1; }
    diskutil enableOwnership "$T" >/dev/null
    mkdir -m 755 "$T/inputs" "$T/results" "$T/mnt"
    echo "SETUP OK $T $dev" ;;
clean)
    spike=${1:?SPIKE}; T=/Volumes/HAOSPIKE-$spike; S=$W/$spike.state
    if [ -f "$S" ]; then
        sed '1!G;h;$!d' "$S" | while read -r kind what; do
            case $kind in
            mount)   umount "$what" 2>/dev/null || diskutil unmount force "$what" >/dev/null 2>&1
                     echo "CLEAN unmounted $what" ;;
            image)   hdiutil detach "$what" >/dev/null 2>&1 || hdiutil detach -force "$what" >/dev/null 2>&1
                     echo "CLEAN detached image $what" ;;
            ramdisk) ;;   # last, after the sweep below
            esac
        done
    fi
    # any image still attached from a file on this spike's RAM disks (T, T-LABEL; a run that died before its state line)
    hdiutil info | awk -v p="$T" '/^image-path/ { want = index($0, p) > 0; next }
        want && $1 ~ /^\/dev\/disk[0-9]+$/ { print $1; want = 0 }' |
    while read -r dev; do hdiutil detach -force "$dev" >/dev/null 2>&1; echo "CLEAN detached leftover $dev"; done
    if [ -f "$S" ]; then
        awk '$1 == "ramdisk" {print $2}' "$S" | while read -r dev; do
            hdiutil detach -force "$dev" >/dev/null 2>&1; echo "CLEAN detached RAM disk $dev"
        done
        rm -f "$S"
    fi
    [ -e "$T" ] && echo "CLEAN WARNING $T still exists" || echo "CLEAN OK $spike" ;;
*)
    sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'; exit 2 ;;
esac
