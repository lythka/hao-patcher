#!/bin/sh
# iasphase.sh (local.iasphase, once at boot): the boot screen's one bar, last hand-over.
# A NetBoot loader's OpenCore can set Apple's install-progress phases (4D1EDE05-…: IASInstallPhaseList
# ssd-download / Boot 1 / Language Chooser, IASCurrentInstallPhase "Boot 1"): boot.efi gives the kernel's bar the
# "Boot 1" range (48..82 %). Recovery's "Installer Progress" reads the current phase when it shows its window (~3 s
# before the Language Chooser starts) and draws that phase's START: with "Boot 1" still set the bar jumps back from
# 82 % to 48 % and only returns to 82 % when the Language Chooser sets its own phase.
# The kernel is done with the variable by now, so the phase moves on to the Language Chooser here, before the window
# exists: it opens at 82 %, where the kernel's bar stopped. Only when the phase is "Boot 1" (a NetBoot); any other
# boot is left alone. Log: /var/log/iasphase.log.
PATH=/usr/bin:/bin:/usr/sbin:/sbin
G=4D1EDE05-38C7-4A6A-9CC6-4BCCA8B38C14
cur=$(nvram "$G:IASCurrentInstallPhase" 2>/dev/null | cut -f2-)
if [ "$cur" != "Boot 1" ]; then
    echo "iasphase: $(date +%T) phase '${cur:-none}': nothing to do"
    exit 0
fi
nvram "$G:IASCurrentInstallPhase=Language Chooser" &&
    echo "iasphase: $(date +%T) phase Boot 1 -> Language Chooser" ||
    echo "iasphase: $(date +%T) nvram write failed: the window opens at the Boot 1 start"
